"""API-key issuance and verification. Testers put the raw key opencode
sends as `Authorization: Bearer <key>` in `options.apiKey`; only its
argon2 hash and a short lookup prefix are ever persisted (see
`app.models.api_key.ApiKey`) - the raw value exists only at issuance
time, printed once by `manage.py issue-key` and never stored anywhere.
"""

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import hash_password, verify_password
from app.models.api_key import ApiKey
from app.models.user import User

_KEY_MARKER = "llmhell_"
# "llmhell_" (8 chars) + 8 random chars from the token - enough entropy
# that a lookup-prefix collision across a handful of testers' keys is
# effectively impossible, while still fitting api_keys.key_prefix (String(16)).
_PREFIX_LENGTH = len(_KEY_MARKER) + 8


def _extract_prefix(raw_key: str) -> str:
    return raw_key[:_PREFIX_LENGTH]


def generate_api_key() -> tuple[str, str]:
    """Returns (raw_key, key_prefix). raw_key is shown to the operator
    exactly once; key_prefix is what gets persisted for lookup."""
    raw_key = _KEY_MARKER + secrets.token_urlsafe(32)
    return raw_key, _extract_prefix(raw_key)


def hash_api_key(raw_key: str) -> str:
    return hash_password(raw_key)


async def authenticate_api_key(db: AsyncSession, raw_key: str) -> tuple[User, ApiKey] | None:
    prefix = _extract_prefix(raw_key)
    candidates = (
        await db.execute(select(ApiKey).where(ApiKey.key_prefix == prefix, ApiKey.revoked_at.is_(None)))
    ).scalars().all()

    for candidate in candidates:
        if verify_password(raw_key, candidate.key_hash):
            user = await db.get(User, candidate.user_id)
            if user is None or not user.is_active:
                return None
            return user, candidate
    return None


async def get_current_key_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> tuple[User, ApiKey]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or malformed Authorization header")

    raw_key = authorization.split(" ", 1)[1].strip()
    result = await authenticate_api_key(db, raw_key)
    if result is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked API key")

    user, api_key = result
    api_key.last_used_at = datetime.now(UTC)
    await db.commit()

    return user, api_key


CurrentKeyUser = Annotated[tuple[User, ApiKey], Depends(get_current_key_user)]


async def require_admin_key(current: CurrentKeyUser) -> tuple[User, ApiKey]:
    user, _ = current
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return current


AdminKeyUser = Annotated[tuple[User, ApiKey], Depends(require_admin_key)]
