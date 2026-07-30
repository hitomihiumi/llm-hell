from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.api_keys import (
    authenticate_api_key,
    generate_api_key,
    get_current_key_user,
    hash_api_key,
    require_admin_key,
)
from app.models.api_key import ApiKey
from app.models.user import User


def test_generate_api_key_prefix_matches_raw_key() -> None:
    raw_key, prefix = generate_api_key()
    assert raw_key.startswith("llmhell_")
    assert raw_key.startswith(prefix)
    assert len(prefix) == 16


async def _make_session(test_db_engine):
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    return session_maker()


async def _seed_user_with_key(db, *, role: str = "user", active: bool = True, revoked: bool = False):
    raw_key, prefix = generate_api_key()
    user = User(username="tester", role=role, is_active=active)
    db.add(user)
    await db.flush()
    api_key = ApiKey(
        user_id=user.id,
        name="test-key",
        key_hash=hash_api_key(raw_key),
        key_prefix=prefix,
        revoked_at=datetime.now(timezone.utc) if revoked else None,
    )
    db.add(api_key)
    await db.commit()
    return raw_key, user, api_key


@pytest.mark.asyncio
async def test_authenticate_api_key_succeeds_for_valid_key(test_db_engine) -> None:
    async with await _make_session(test_db_engine) as db:
        raw_key, user, _ = await _seed_user_with_key(db)

        result = await authenticate_api_key(db, raw_key)
        assert result is not None
        found_user, found_key = result
        assert found_user.id == user.id


@pytest.mark.asyncio
async def test_authenticate_api_key_rejects_wrong_key(test_db_engine) -> None:
    async with await _make_session(test_db_engine) as db:
        await _seed_user_with_key(db)
        assert await authenticate_api_key(db, "llmhell_not-the-right-key") is None


@pytest.mark.asyncio
async def test_authenticate_api_key_rejects_revoked_key(test_db_engine) -> None:
    async with await _make_session(test_db_engine) as db:
        raw_key, _, _ = await _seed_user_with_key(db, revoked=True)
        assert await authenticate_api_key(db, raw_key) is None


@pytest.mark.asyncio
async def test_authenticate_api_key_rejects_inactive_user(test_db_engine) -> None:
    async with await _make_session(test_db_engine) as db:
        raw_key, _, _ = await _seed_user_with_key(db, active=False)
        assert await authenticate_api_key(db, raw_key) is None


@pytest.mark.asyncio
async def test_get_current_key_user_rejects_missing_header(test_db_engine) -> None:
    async with await _make_session(test_db_engine) as db:
        with pytest.raises(HTTPException) as exc_info:
            await get_current_key_user(db, authorization=None)
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_key_user_rejects_malformed_header(test_db_engine) -> None:
    async with await _make_session(test_db_engine) as db:
        with pytest.raises(HTTPException) as exc_info:
            await get_current_key_user(db, authorization="Token abc123")
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_key_user_accepts_valid_bearer_and_updates_last_used(test_db_engine) -> None:
    async with await _make_session(test_db_engine) as db:
        raw_key, user, api_key = await _seed_user_with_key(db)

        found_user, found_key = await get_current_key_user(db, authorization=f"Bearer {raw_key}")
        assert found_user.id == user.id
        assert found_key.last_used_at is not None


@pytest.mark.asyncio
async def test_require_admin_key_rejects_non_admin(test_db_engine) -> None:
    async with await _make_session(test_db_engine) as db:
        raw_key, _, _ = await _seed_user_with_key(db, role="user")
        current = await get_current_key_user(db, authorization=f"Bearer {raw_key}")

        with pytest.raises(HTTPException) as exc_info:
            await require_admin_key(current)
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_key_accepts_admin(test_db_engine) -> None:
    async with await _make_session(test_db_engine) as db:
        raw_key, user, _ = await _seed_user_with_key(db, role="admin")
        current = await get_current_key_user(db, authorization=f"Bearer {raw_key}")

        found_user, _ = await require_admin_key(current)
        assert found_user.id == user.id
