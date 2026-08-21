"""Connecting a user's own Google and GitLab accounts.

Two providers, two very different shapes, and the difference is not
incidental:

  * **GitLab** is a token the user already holds. They paste it, and this
    verifies it against the instance before storing it - because a PAT with
    the wrong scopes fails later, inside a search, as an empty source with a
    403 nobody reads.

  * **Google** is an OAuth consent the user has to give. The MCP server owns
    that flow and writes its own token files, so this drives it rather than
    reimplementing it: `manage_accounts authenticate` produces a URL, the
    user visits it, and the server stores the refresh token where it keeps
    all the others. What is stored here is only the fact of the connection -
    which account, which scopes - so the interface can say whether Drive will
    work without asking Google every time.

No route in this file ever returns a secret. `status()` in the service layer
is built from a row that never had one, which is why that guarantee is
structural rather than a review comment.
"""

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.openai_proxy import get_http_client
from app.core.config import get_settings
from app.core.crypto import CredentialEncryptionError
from app.core.db import get_db
from app.core.sessions import CurrentUser
from app.models.user_credential import PROVIDER_GITLAB, PROVIDER_GOOGLE, PROVIDERS
from app.schemas.credentials import (
    CredentialStatusOut,
    GitLabTokenIn,
    GoogleAuthStartOut,
)
from app.services import credentials as credential_service
from app.services.mcp.google import GoogleWorkspaceConnector
from app.services.mcp.registry import McpRegistry, get_mcp_registry
from app.services.mcp.transport import summarise_exception

logger = logging.getLogger("llmhell.api.credentials")

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


@router.get("", response_model=list[CredentialStatusOut])
async def list_credentials(
    current: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return await credential_service.status(db, user=current, settings=get_settings())


@router.put("/gitlab", response_model=CredentialStatusOut)
async def connect_gitlab(
    payload: GitLabTokenIn,
    current: CurrentUser,
    db: AsyncSession = Depends(get_db),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """Verify a personal access token, then store it.

    Verified first, and this is the point of the route. A token with the
    wrong scopes is accepted by GitLab's `/user` and refused by everything
    this application actually calls, so the check asks for a project listing
    too - which is what a search does.
    """
    settings = get_settings()
    api_url = (payload.api_url or settings.gitlab_api_url).rstrip("/")
    token = payload.token.strip()
    if not token:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "the token is empty")

    identity = await _verify_gitlab(http_client, api_url=api_url, token=token)

    try:
        await credential_service.store(
            db,
            user=current,
            provider=PROVIDER_GITLAB,
            secret=token,
            settings=settings,
            account=identity.get("username"),
            detail={"api_url": api_url, "name": identity.get("name")},
        )
    except CredentialEncryptionError as exc:
        # A configuration problem, not the user's. 503 rather than 400: there
        # is nothing they can change about their request that would help.
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return await _one(db, current, PROVIDER_GITLAB)


@router.post("/google/start", response_model=GoogleAuthStartOut)
async def start_google(
    current: CurrentUser,
    db: AsyncSession = Depends(get_db),
    registry: McpRegistry = Depends(get_mcp_registry),
) -> dict[str, Any]:
    """Whether this user's Google account can be searched as.

    Not an OAuth start, despite the name, and the docstring says so because
    the shape is surprising: the Workspace MCP server owns consent and cannot
    run it headless, so what this reports is whether the server already holds
    credentials for the address - and, when it does not, what to do about it.
    Nothing secret crosses this route in either direction.

    Confirm with `POST /google/confirm` to record the connection.
    """
    connector = registry.get("google_drive")
    if not isinstance(connector, GoogleWorkspaceConnector):
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE, "the Google source is not configured")

    email = (current.email or "").strip()
    if not email:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "your account has no email address, and Google's tools are addressed by one",
        )

    try:
        report = await connector.begin_account_auth(email)
    except Exception as exc:  # noqa: BLE001 - reported, never raised through
        raise HTTPException(
            http_status.HTTP_502_BAD_GATEWAY,
            f"could not start Google authentication: {summarise_exception(exc)}",
        ) from exc

    return {
        "email": email,
        "url": report.get("url"),
        "authenticated": bool(report.get("authenticated")),
        "message": report.get("message", ""),
    }


@router.post("/google/confirm", response_model=CredentialStatusOut)
async def confirm_google(
    current: CurrentUser,
    db: AsyncSession = Depends(get_db),
    registry: McpRegistry = Depends(get_mcp_registry),
) -> dict[str, Any]:
    """Record that consent was given, once the server can see the account.

    Asked of the MCP server rather than believed: a user who closed the
    consent tab would otherwise have a row saying they were connected and a
    Drive search that returned nothing.
    """
    settings = get_settings()
    connector = registry.get("google_drive")
    if not isinstance(connector, GoogleWorkspaceConnector):
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE, "the Google source is not configured")

    email = (current.email or "").strip()
    account = await connector.account_status(email)
    if not account.get("authenticated"):
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            account.get("message") or f"Google has no authenticated account for {email}",
        )

    try:
        await credential_service.store(
            db,
            user=current,
            provider=PROVIDER_GOOGLE,
            # The refresh token stays in the MCP server's own store; what is
            # kept here is the address that reaches it. Encrypted anyway,
            # because an email is still the user's, and because one code path
            # for every credential is worth more than the bytes saved.
            secret=email,
            settings=settings,
            account=email,
            detail={"scopes": account.get("scopes", []), "held_by": "google-mcp"},
        )
    except CredentialEncryptionError as exc:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return await _one(db, current, PROVIDER_GOOGLE)


@router.delete("/{provider}", status_code=http_status.HTTP_204_NO_CONTENT)
async def disconnect(
    provider: str,
    current: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    if provider not in PROVIDERS:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"unknown provider: {provider}")
    await credential_service.forget(db, user=current, provider=provider)


# --- helpers -------------------------------------------------------------------


async def _verify_gitlab(http_client: httpx.AsyncClient, *, api_url: str, token: str) -> dict[str, Any]:
    """Who the token belongs to, or an error the user can act on."""
    headers = {"PRIVATE-TOKEN": token}
    try:
        response = await http_client.get(f"{api_url}/user", headers=headers, timeout=15.0)
    except httpx.HTTPError as exc:
        raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, f"could not reach {api_url}: {exc}") from exc

    if response.status_code == 401:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "GitLab rejected that token as invalid or expired")
    if response.status_code == 403:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "that token is valid but lacks the `read_api` scope, which searching needs",
        )
    if response.status_code >= 400:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"GitLab answered {response.status_code} when asked who the token belongs to",
        )

    identity = response.json()

    # The scope check that matters. `/user` answers for almost any token; the
    # project listing is what a search actually does, and a token that cannot
    # do it would fail later as an empty source rather than as a bad token.
    try:
        projects = await http_client.get(f"{api_url}/projects", headers=headers, params={"per_page": 1}, timeout=15.0)
    except httpx.HTTPError:
        # The identity check passed; a flaky second call is not grounds to
        # refuse a token that is probably fine.
        return identity
    if projects.status_code == 403:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "that token cannot list projects - it needs the `read_api` scope",
        )
    return identity


async def _one(db: AsyncSession, user, provider: str) -> dict[str, Any]:
    report = await credential_service.status(db, user=user, settings=get_settings())
    return next(entry for entry in report if entry["provider"] == provider)
