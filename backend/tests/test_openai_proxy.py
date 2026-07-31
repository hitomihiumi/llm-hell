import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.openai_proxy import get_http_client
from app.main import app as fastapi_app
from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.models.llm_request import LlmRequest


async def _seed_endpoint(test_db_engine, **overrides) -> str:
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        fields = {
            "name": "mock-glm",
            "base_url": "http://mock-vllm/v1",
            "model_id": "glm-4.7",
            "role": "executor",
            "ctx_window": 32768,
            "enabled": True,
            "reasoning_profile": DEFAULT_REASONING_PROFILE,
            **overrides,
        }
        endpoint = ModelEndpoint(**fields)
        session.add(endpoint)
        await session.commit()
        await session.refresh(endpoint)
        return endpoint.id


@pytest.fixture(autouse=True)
def _use_mock_upstream(mock_vllm_http_client):
    fastapi_app.dependency_overrides[get_http_client] = lambda: mock_vllm_http_client
    yield
    fastapi_app.dependency_overrides.pop(get_http_client, None)


@pytest.mark.asyncio
async def test_list_models_requires_auth(api_client) -> None:
    response = await api_client.get("/v1/models")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_models_publishes_bare_model_id(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)

    response = await authed_client.get("/v1/models")
    assert response.status_code == 200
    ids = {m["id"] for m in response.json()["data"]}
    assert ids == {"glm-4.7"}


@pytest.mark.asyncio
async def test_list_models_excludes_disabled_endpoints(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine, enabled=False)

    response = await authed_client.get("/v1/models")
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_chat_completions_requires_auth(api_client) -> None:
    response = await api_client.post("/v1/chat/completions", json={"model": "glm-4.7", "messages": []})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_rejects_missing_model(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)
    response = await authed_client.post("/v1/chat/completions", json={"messages": []})
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "missing_model"


@pytest.mark.asyncio
async def test_chat_completions_rejects_unknown_model(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)
    response = await authed_client.post(
        "/v1/chat/completions", json={"model": "not-a-real-model", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "unknown_model"


@pytest.mark.asyncio
async def test_chat_completions_non_streaming_passthrough(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)

    response = await authed_client.post(
        "/v1/chat/completions",
        json={
            "model": "glm-4.7",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-session-affinity": "sess-non-stream"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "mock completion" in body["choices"][0]["message"]["content"]
    assert body["usage"]["prompt_tokens"] > 0

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        row = (
            await session.execute(select(LlmRequest).where(LlmRequest.session_id == "sess-non-stream"))
        ).scalar_one()
        assert row.model == "glm-4.7"
        assert row.reasoning_level == "off"
        assert row.stream is False
        assert row.status_code == 200
        assert row.tokens_prompt > 0
        assert row.tokens_completion > 0
        assert row.duration_ms >= 0


@pytest.mark.asyncio
async def test_chat_completions_streaming_passthrough(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)

    async with authed_client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "glm-4.7",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-session-affinity": "sess-stream", "x-parent-session-id": "parent-1"},
    ) as response:
        assert response.status_code == 200
        raw_text = ""
        async for chunk in response.aiter_text():
            raw_text += chunk

    # Passthrough is byte-for-byte, so each word of the mock's canned
    # completion text arrives as its own SSE delta chunk rather than
    # concatenated - check for the words themselves, not a contiguous
    # phrase spanning JSON chunk boundaries.
    assert "data: " in raw_text
    assert '"content": "mock ' in raw_text
    assert '"content": "completion ' in raw_text
    assert "[DONE]" in raw_text

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        row = (
            await session.execute(select(LlmRequest).where(LlmRequest.session_id == "sess-stream"))
        ).scalar_one()
        assert row.parent_session_id == "parent-1"
        assert row.stream is True
        assert row.tokens_prompt > 0
        assert row.tokens_completion > 0
        assert row.ttft_ms is not None
        assert row.finish_reason == "stop"


@pytest.mark.asyncio
async def test_chat_completions_session_id_falls_back_without_header(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)

    response = await authed_client.post(
        "/v1/chat/completions",
        json={"model": "glm-4.7", "stream": False, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        row = (await session.execute(select(LlmRequest))).scalars().first()
        assert row.session_id.startswith("fallback-")
