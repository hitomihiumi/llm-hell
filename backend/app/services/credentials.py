"""Reading and writing the credentials a user supplied about themselves.

Every route and every connector goes through here rather than touching the
table, so there is exactly one place that decrypts and exactly one place that
decides what a caller is allowed to see. `status()` is what the API returns
and cannot leak a secret because it never has one; `secret_for()` is what the
connectors call and returns nothing else.

**Falling back is a feature.** A deployment with no per-user credentials at
all - one person, tokens in `.env`, which is how this stack has always run -
must keep working exactly as it did. So a missing credential is never an
error here: it is `None`, and the caller uses the deployment-wide token as
before.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import CredentialCipher, CredentialEncryptionError
from app.models.user import User
from app.models.user_credential import PROVIDERS, UserCredential

logger = logging.getLogger("llmhell.credentials")


def cipher_for(settings: Settings) -> CredentialCipher | None:
    """The deployment's cipher, or None when per-user credentials are off.

    None rather than an exception, because "this install does not use them"
    and "this install is misconfigured" are different situations and only the
    second is worth interrupting anybody over.
    """
    if not settings.credentials_encryption_key:
        return None
    return CredentialCipher(settings.credentials_encryption_key)


async def store(
    db: AsyncSession,
    *,
    user: User,
    provider: str,
    secret: str,
    settings: Settings,
    account: str | None = None,
    expires_at: datetime | None = None,
    detail: dict[str, Any] | None = None,
) -> UserCredential:
    """Save a credential, replacing whatever was there for that provider."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown credential provider: {provider!r}")
    cipher = cipher_for(settings)
    if cipher is None:
        raise CredentialEncryptionError(
            "CREDENTIALS_ENCRYPTION_KEY is not set, so per-user credentials cannot be stored."
        )

    existing = await _row(db, user_id=user.id, provider=provider)
    if existing is None:
        existing = UserCredential(user_id=user.id, provider=provider, secret="")
        db.add(existing)

    existing.secret = cipher.encrypt(secret)
    existing.account = account
    existing.expires_at = expires_at
    existing.detail = detail or {}
    existing.last_verified_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(existing)
    return existing


async def secret_for(db: AsyncSession, *, user: User, provider: str, settings: Settings) -> str | None:
    """The user's credential for a provider, or None to fall back.

    Every failure returns None. A key that has been rotated, a row that will
    not decrypt, a provider the user never set up - none of those should take
    a search down, because the deployment-wide token is still there and still
    works. The failure is logged once and the search proceeds.
    """
    cipher = cipher_for(settings)
    if cipher is None:
        return None

    row = await _row(db, user_id=user.id, provider=provider)
    if row is None:
        return None

    try:
        return cipher.decrypt(row.secret)
    except CredentialEncryptionError as exc:
        logger.warning("could not read %s credential for %s: %s", provider, user.id, exc)
        return None


async def status(db: AsyncSession, *, user: User, settings: Settings) -> list[dict[str, Any]]:
    """What the interface may show: one entry per provider, secrets absent.

    Every provider is listed, including the ones with nothing stored, because
    "you have not connected GitLab" is the single most useful thing this can
    say and an absent row would render as an absent list item.
    """
    rows = {row.provider: row for row in await _rows(db, user_id=user.id)}
    enabled = bool(settings.credentials_encryption_key)

    report: list[dict[str, Any]] = []
    for provider in PROVIDERS:
        row = rows.get(provider)
        entry: dict[str, Any] = {
            "provider": provider,
            "connected": row is not None,
            "account": row.account if row else None,
            "expires_at": row.expires_at if row else None,
            "last_verified_at": row.last_verified_at if row else None,
            "detail": row.detail if row else {},
            # Said per provider rather than once, so the interface can explain
            # why a connect button does nothing without a second request.
            "storage_available": enabled,
        }
        if row is not None and row.expires_at is not None:
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                # SQLite hands back naive datetimes even from a timezone-aware
                # column, and comparing naive to aware raises.
                expires_at = expires_at.replace(tzinfo=UTC)
            entry["expired"] = expires_at <= datetime.now(UTC)
        else:
            entry["expired"] = False
        report.append(entry)
    return report


async def forget(db: AsyncSession, *, user: User, provider: str) -> bool:
    """Drop a credential. Returns whether there was one."""
    row = await _row(db, user_id=user.id, provider=provider)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def _row(db: AsyncSession, *, user_id: str, provider: str) -> UserCredential | None:
    return (
        await db.execute(
            select(UserCredential).where(UserCredential.user_id == user_id, UserCredential.provider == provider)
        )
    ).scalar_one_or_none()


async def _rows(db: AsyncSession, *, user_id: str) -> list[UserCredential]:
    return list((await db.execute(select(UserCredential).where(UserCredential.user_id == user_id))).scalars().all())
