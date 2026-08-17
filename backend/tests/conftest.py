import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.api_keys import generate_api_key, hash_api_key
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import hash_password
from app.core.sessions import create_session
from app.main import app as fastapi_app
from app.models import Base
from app.models.api_key import ApiKey
from app.models.user import User

TEST_PASSWORD = "correct-horse-battery-staple"

_MOCK_VLLM_PATH = Path(__file__).resolve().parents[2] / "tools" / "mock_vllm.py"


def _load_mock_vllm_app():
    spec = importlib.util.spec_from_file_location("mock_vllm", _MOCK_VLLM_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["mock_vllm"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.app


@pytest.fixture
def mock_vllm_http_client():
    app = _load_mock_vllm_app()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://mock-vllm/v1")


@pytest_asyncio.fixture
async def test_db_engine():
    """A throwaway in-memory SQLite database standing in for Postgres in
    API-level tests, so the request/response cycle (routing, dependency
    injection) can be exercised without Docker."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def api_client(test_db_engine):
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with session_maker() as session:
            yield session

    fastapi_app.dependency_overrides[get_db] = override_get_db

    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    fastapi_app.dependency_overrides.clear()


async def _make_keyed_client(api_client, test_db_engine, *, username: str, role: str) -> httpx.AsyncClient:
    raw_key, prefix = generate_api_key()

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        user = User(username=username, role=role)
        session.add(user)
        await session.flush()
        session.add(
            ApiKey(user_id=user.id, name="test-key", key_hash=hash_api_key(raw_key), key_prefix=prefix)
        )
        await session.commit()

    api_client.headers["Authorization"] = f"Bearer {raw_key}"
    return api_client


@pytest_asyncio.fixture
async def authed_client(api_client, test_db_engine):
    """An `api_client` whose requests already carry a valid API-key
    Authorization header for a plain "user"-role tester."""
    return await _make_keyed_client(api_client, test_db_engine, username="tester", role="user")


@pytest_asyncio.fixture
async def admin_authed_client(api_client, test_db_engine):
    """Same as `authed_client` but for an "admin"-role tester."""
    return await _make_keyed_client(api_client, test_db_engine, username="admin-tester", role="admin")


async def _make_session_client(
    api_client, test_db_engine, *, username: str, role: str
) -> httpx.AsyncClient:
    """The cookie-session counterpart of `_make_keyed_client`: creates a user
    with a real argon2 password hash and a live session row, then sets both
    the session cookie and the CSRF cookie/header pair the way a browser
    would after logging in."""
    settings = get_settings()
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        user = User(username=username, role=role, password_hash=hash_password(TEST_PASSWORD))
        session.add(user)
        await session.flush()
        _, raw_token = await create_session(session, user, user_agent="pytest", ip="127.0.0.1")

    csrf_token = "test-csrf-token"
    api_client.cookies.set(settings.session_cookie_name, raw_token)
    api_client.cookies.set(settings.csrf_cookie_name, csrf_token)
    api_client.headers["X-CSRF-Token"] = csrf_token
    return api_client


@pytest_asyncio.fixture
async def session_client(api_client, test_db_engine):
    """An `api_client` already logged in as a plain "user"-role account."""
    return await _make_session_client(api_client, test_db_engine, username="web-user", role="user")


@pytest_asyncio.fixture
async def admin_session_client(api_client, test_db_engine):
    """Same as `session_client` but for an "admin"-role account."""
    return await _make_session_client(api_client, test_db_engine, username="web-admin", role="admin")


@pytest.fixture
def settings_override():
    """Patch Settings fields for one test.

    `get_settings` is `@lru_cache`d and read at import time by several
    modules, so a test that changes configuration has to clear the cache on
    the way in AND on the way out - otherwise the mutated instance leaks
    into every later test in the session.

        def test_x(settings_override):
            settings_override(enable_mcp_debug=True)
    """
    get_settings.cache_clear()
    applied = get_settings()

    def _apply(**kwargs):
        for key, value in kwargs.items():
            setattr(applied, key, value)
        return applied

    yield _apply
    get_settings.cache_clear()
