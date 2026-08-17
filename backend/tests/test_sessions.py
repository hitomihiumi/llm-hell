from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.core.sessions import (
    create_session,
    generate_session_token,
    hash_session_token,
    resolve_session,
    revoke_session,
)
from app.models.session import UserSession
from app.models.user import User


@pytest.fixture
def session_maker(test_db_engine):
    return async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)


async def _make_user(session_maker, *, username: str = "alice", is_active: bool = True) -> User:
    async with session_maker() as db:
        user = User(username=username, is_active=is_active, password_hash=hash_password("pw"))
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


def test_generated_token_matches_its_hash():
    raw, token_hash = generate_session_token()
    assert token_hash == hash_session_token(raw)
    # sha256 hex, which is what the String(64) column is sized for.
    assert len(token_hash) == 64


def test_two_tokens_are_distinct():
    assert generate_session_token()[0] != generate_session_token()[0]


async def test_create_session_persists_only_the_hash(session_maker):
    user = await _make_user(session_maker)
    async with session_maker() as db:
        session, raw = await create_session(db, user, user_agent="pytest", ip="10.0.0.1")

    async with session_maker() as db:
        stored = await db.get(UserSession, session.id)
        assert stored is not None
        assert stored.token_hash == hash_session_token(raw)
        # The raw token must not be recoverable from the row.
        assert raw not in (stored.token_hash, stored.user_agent, stored.ip)


async def test_resolve_session_returns_the_user(session_maker):
    user = await _make_user(session_maker)
    async with session_maker() as db:
        _, raw = await create_session(db, user)

    async with session_maker() as db:
        resolved = await resolve_session(db, raw)
        assert resolved is not None
        assert resolved.id == user.id


@pytest.mark.parametrize("bad_token", ["", "not-a-real-token"])
async def test_resolve_session_rejects_unknown_tokens(session_maker, bad_token):
    async with session_maker() as db:
        assert await resolve_session(db, bad_token) is None


async def test_resolve_session_rejects_revoked(session_maker):
    user = await _make_user(session_maker)
    async with session_maker() as db:
        _, raw = await create_session(db, user)

    async with session_maker() as db:
        await revoke_session(db, raw)

    async with session_maker() as db:
        assert await resolve_session(db, raw) is None


async def test_revoke_session_is_idempotent(session_maker):
    """Logout must never fail, including a double-click or a stale cookie."""
    user = await _make_user(session_maker)
    async with session_maker() as db:
        _, raw = await create_session(db, user)

    async with session_maker() as db:
        await revoke_session(db, raw)
        await revoke_session(db, raw)
        await revoke_session(db, "never-existed")
        await revoke_session(db, "")


async def test_resolve_session_rejects_expired(session_maker):
    user = await _make_user(session_maker)
    async with session_maker() as db:
        _, raw = await create_session(db, user, ttl_seconds=-1)

    async with session_maker() as db:
        assert await resolve_session(db, raw) is None


async def test_resolve_session_rejects_deactivated_user(session_maker):
    """A session outlives the account being switched off, so the check has
    to happen at resolve time rather than only at login."""
    user = await _make_user(session_maker)
    async with session_maker() as db:
        _, raw = await create_session(db, user)

    async with session_maker() as db:
        stored = await db.get(User, user.id)
        stored.is_active = False
        await db.commit()

    async with session_maker() as db:
        assert await resolve_session(db, raw) is None


async def test_resolve_session_refreshes_last_seen_when_stale(session_maker):
    user = await _make_user(session_maker)
    async with session_maker() as db:
        session, raw = await create_session(db, user)

    stale = datetime.now(timezone.utc) - timedelta(hours=1)
    async with session_maker() as db:
        stored = await db.get(UserSession, session.id)
        stored.last_seen_at = stale
        await db.commit()

    async with session_maker() as db:
        await resolve_session(db, raw)

    async with session_maker() as db:
        stored = await db.get(UserSession, session.id)
        last_seen = stored.last_seen_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        assert last_seen > stale


async def test_resolve_session_does_not_rewrite_fresh_last_seen(session_maker):
    """The whole point of the 60s window: a chatty SPA must not cause a
    COMMIT per request."""
    user = await _make_user(session_maker)
    async with session_maker() as db:
        session, raw = await create_session(db, user)

    async with session_maker() as db:
        await resolve_session(db, raw)
    async with session_maker() as db:
        stored = await db.get(UserSession, session.id)
        first_seen = stored.last_seen_at

    async with session_maker() as db:
        await resolve_session(db, raw)
    async with session_maker() as db:
        stored = await db.get(UserSession, session.id)
        assert stored.last_seen_at == first_seen


async def test_sessions_are_per_user(session_maker):
    alice = await _make_user(session_maker, username="alice")
    bob = await _make_user(session_maker, username="bob")
    async with session_maker() as db:
        _, alice_raw = await create_session(db, alice)
        _, bob_raw = await create_session(db, bob)

    async with session_maker() as db:
        assert (await resolve_session(db, alice_raw)).id == alice.id
        assert (await resolve_session(db, bob_raw)).id == bob.id

    # Revoking one leaves the other alone.
    async with session_maker() as db:
        await revoke_session(db, alice_raw)
    async with session_maker() as db:
        assert await resolve_session(db, alice_raw) is None
        assert (await resolve_session(db, bob_raw)).id == bob.id


async def test_long_user_agent_and_ip_are_truncated_to_column_width(session_maker):
    """Postgres rejects an over-long value outright, so a browser with an
    absurd UA string would 500 the login route rather than sign in."""
    user = await _make_user(session_maker)
    async with session_maker() as db:
        session, _ = await create_session(db, user, user_agent="U" * 500, ip="I" * 200)

    async with session_maker() as db:
        stored = await db.get(UserSession, session.id)
        assert len(stored.user_agent) == 256
        assert len(stored.ip) == 64


async def test_token_hash_is_unique_across_sessions(session_maker):
    user = await _make_user(session_maker)
    async with session_maker() as db:
        await create_session(db, user)
        await create_session(db, user)
        hashes = (await db.execute(select(UserSession.token_hash))).scalars().all()
        assert len(hashes) == len(set(hashes)) == 2
