"""Cookie-session authentication for the knowledge-base web app.

Deliberately separate from `app.core.api_keys`, which is untouched: the
OpenAI-compatible proxy on `/v1/*` still authenticates opencode by bearer
API key, and the two mechanisms should not be able to break each other. The
web app never sends a bearer token; the proxy never sends a cookie.

See `app.models.session` for why the session token is hashed with SHA-256
rather than argon2.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.models.session import UserSession
from app.models.user import User

SESSION_TOKEN_BYTES = 32

# How stale `last_seen_at` is allowed to get. Writing it on every request -
# which is what api_keys.py does with `last_used_at` - means a COMMIT per
# request, and a single-page app fires several requests per interaction.
# A minute's resolution is plenty for "when was this session last active".
LAST_SEEN_REFRESH_SECONDS = 60


def generate_session_token() -> tuple[str, str]:
    """Returns (raw_token, token_hash). The raw token goes into the cookie
    and is never persisted."""
    raw = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    return raw, hash_session_token(raw)


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


async def create_session(
    db: AsyncSession,
    user: User,
    *,
    ttl_seconds: int | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[UserSession, str]:
    settings = get_settings()
    raw, token_hash = generate_session_token()
    session = UserSession(
        user_id=user.id,
        token_hash=token_hash,
        user_agent=(user_agent or None) and user_agent[:256],
        ip=(ip or None) and ip[:64],
        expires_at=datetime.now(UTC)
        + timedelta(seconds=ttl_seconds if ttl_seconds is not None else settings.session_ttl_seconds),
    )
    db.add(session)
    await db.commit()
    return session, raw


async def resolve_session(db: AsyncSession, raw_token: str) -> User | None:
    """The authenticated user for a raw cookie value, or None.

    Returns None for every failure mode - unknown, revoked, expired, or
    belonging to a deactivated user - because the caller has nothing useful
    to do with the distinction and a 401 should not describe which one it
    was.
    """
    if not raw_token:
        return None

    session = (
        await db.execute(select(UserSession).where(UserSession.token_hash == hash_session_token(raw_token)))
    ).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return None

    now = datetime.now(UTC)
    # SQLite via aiosqlite can hand back naive datetimes even from a
    # DateTime(timezone=True) column, and comparing naive to aware raises.
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        return None

    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None

    last_seen = session.last_seen_at
    if last_seen is not None and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    if last_seen is None or (now - last_seen).total_seconds() > LAST_SEEN_REFRESH_SECONDS:
        session.last_seen_at = now
        await db.commit()

    return user


async def revoke_session(db: AsyncSession, raw_token: str) -> None:
    """Idempotent: revoking an unknown or already-revoked token is a no-op,
    so logout never fails."""
    if not raw_token:
        return
    session = (
        await db.execute(select(UserSession).where(UserSession.token_hash == hash_session_token(raw_token)))
    ).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return
    session.revoked_at = datetime.now(UTC)
    await db.commit()


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name, "")
    user = await resolve_session(db, raw_token)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(current: CurrentUser) -> User:
    if current.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return current


AdminUser = Annotated[User, Depends(require_admin)]


async def require_csrf(request: Request) -> None:
    """Double-submit CSRF check for cookie-authenticated state-changing
    routes.

    `SameSite=Lax` already blocks the realistic cross-site POST, and a JSON
    content type forces a preflight on top of that. This is the third layer,
    and it is here because it costs almost nothing: login also sets a
    readable `kb_csrf` cookie, and the frontend echoes it back in a header.
    An attacker on another origin can cause the cookie to be *sent* but
    cannot *read* it, so it cannot fill in the header.

    Safe methods are exempt, and so is any request with no session cookie -
    an unauthenticated request has nothing to forge.
    """
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return

    settings = get_settings()
    if not request.cookies.get(settings.session_cookie_name):
        return

    cookie_token = request.cookies.get(settings.csrf_cookie_name, "")
    header_token = request.headers.get("x-csrf-token", "")
    if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
