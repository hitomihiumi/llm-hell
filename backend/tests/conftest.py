import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.api.projects as projects_module
from app.core.db import get_db
from app.main import app as fastapi_app
from app.models import Base
from app.models.user import User
from app.core.security import hash_password

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
    injection, cookies) can be exercised without Docker."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def api_client(test_db_engine, tmp_path):
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with session_maker() as session:
            yield session

    fastapi_app.dependency_overrides[get_db] = override_get_db
    original_projects_dir = projects_module.settings.projects_dir
    projects_module.settings.projects_dir = str(tmp_path)

    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    fastapi_app.dependency_overrides.clear()
    projects_module.settings.projects_dir = original_projects_dir


@pytest_asyncio.fixture
async def logged_in_client(api_client, test_db_engine):
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        session.add(User(username="tester", password_hash=hash_password("password123"), role="user"))
        await session.commit()

    response = await api_client.post("/api/auth/login", json={"username": "tester", "password": "password123"})
    assert response.status_code == 200
    return api_client
