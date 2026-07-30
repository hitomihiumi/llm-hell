import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.api_keys import generate_api_key, hash_api_key
from app.core.db import get_db
from app.main import app as fastapi_app
from app.models import Base
from app.models.api_key import ApiKey
from app.models.user import User

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
