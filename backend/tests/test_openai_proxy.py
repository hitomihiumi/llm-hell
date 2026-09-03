from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.openai_proxy import get_http_client
from app.main import app as fastapi_app
from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.models.llm_request import LlmRequest
from app.schemas.search import SearchHit
from app.services.llm import coding_provider
from app.services.mcp.registry import McpRegistry, get_mcp_registry
from app.services.search.service import FederatedSearch


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
async def test_list_models_publishes_one_id_per_endpoint(authed_client, test_db_engine) -> None:
    """No per-level suffixes: the level is chosen by opencode's own effort
    selector on the request, not by picking a different model."""
    await _seed_endpoint(test_db_engine)

    response = await authed_client.get("/v1/models")
    assert response.status_code == 200
    ids = {m["id"] for m in response.json()["data"]}
    # The backend itself is advertised as a synthetic coding provider.
    assert ids == {"glm-4.7", coding_provider.CODING_PROVIDER_MODEL_ID}


@pytest.mark.asyncio
async def test_list_models_unaffected_by_levels_config(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine, reasoning_profile={"parse": {"mode": "auto"}})

    response = await authed_client.get("/v1/models")
    ids = {m["id"] for m in response.json()["data"]}
    assert ids == {"glm-4.7", coding_provider.CODING_PROVIDER_MODEL_ID}


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
        assert row.reasoning_level == "default"
        assert row.stream is False
        assert row.status_code == 200
        assert row.tokens_prompt > 0
        assert row.tokens_completion > 0
        assert row.duration_ms >= 0


@pytest.mark.asyncio
async def test_client_reasoning_effort_reaches_upstream_and_is_recorded(
    authed_client, test_db_engine
) -> None:
    """opencode's built-in effort selector sends reasoning_effort. The proxy
    must forward it untouched - it used to overwrite it from the model id,
    which made the selector do nothing."""
    await _seed_endpoint(test_db_engine)

    response = await authed_client.post(
        "/v1/chat/completions",
        json={
            "model": "glm-4.7",
            "reasoning_effort": "high",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-session-affinity": "sess-high"},
    )
    assert response.status_code == 200
    # The mock only reasons when it receives a non-"none" effort, so this
    # proves the field survived the hop rather than being stripped.
    assert response.json()["choices"][0]["message"].get("reasoning_content")

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        row = (
            await session.execute(select(LlmRequest).where(LlmRequest.session_id == "sess-high"))
        ).scalar_one()
        assert row.model == "glm-4.7"
        assert row.reasoning_level == "high"
        assert row.tokens_reasoning > 0


@pytest.mark.asyncio
async def test_request_without_effort_records_default(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)

    response = await authed_client.post(
        "/v1/chat/completions",
        json={"model": "glm-4.7", "stream": False, "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-session-affinity": "sess-default"},
    )
    assert response.status_code == 200

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        row = (
            await session.execute(select(LlmRequest).where(LlmRequest.session_id == "sess-default"))
        ).scalar_one()
        assert row.reasoning_level == "default"


@pytest.mark.asyncio
async def test_effort_is_stripped_for_endpoints_with_levels_disabled(
    authed_client, test_db_engine
) -> None:
    """The escape hatch for a model that breaks on reasoning_effort at any
    value (GLM-4.7 looped): clearing `levels` makes the proxy drop the field
    instead of forwarding whatever the client picked."""
    await _seed_endpoint(test_db_engine, reasoning_profile={"parse": {"mode": "auto"}})

    response = await authed_client.post(
        "/v1/chat/completions",
        json={
            "model": "glm-4.7",
            "reasoning_effort": "high",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    # Stripped upstream, so the mock produced no reasoning despite the client
    # asking for "high".
    assert not response.json()["choices"][0]["message"].get("reasoning_content")


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


@pytest.mark.asyncio
async def test_chat_completions_coding_provider_non_streaming(authed_client, test_db_engine) -> None:
    """The backend can answer as an OpenAI-compatible model via its own RAG."""
    await _seed_endpoint(test_db_engine)
    fastapi_app.dependency_overrides[get_mcp_registry] = lambda: MagicMock(spec=McpRegistry)

    federated = FederatedSearch(
        query_id="q-1",
        query="hello",
        hits=[SearchHit(id="kb:1", source="postgres_kb", title="doc", snippet="body", score=1.0)],
        source_status=[],
        duration_ms=10,
        queries=["hello"],
    )
    answer = coding_provider.answer_service.AnswerResult(
        text="The answer is 42.",
        model="glm-4.7",
        prompt_tokens=100,
        completion_tokens=10,
    )

    try:
        with (
            patch.object(coding_provider.planner, "plan_queries", new=AsyncMock(return_value=["hello"])),
            patch.object(coding_provider, "federated_search", new=AsyncMock(return_value=(federated, MagicMock()))),
            patch.object(
                coding_provider.answer_service, "synthesize", new=AsyncMock(return_value=answer)
            ),
        ):
            response = await authed_client.post(
                "/v1/chat/completions",
                json={
                    "model": coding_provider.CODING_PROVIDER_MODEL_ID,
                    "stream": False,
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_mcp_registry, None)

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == coding_provider.CODING_PROVIDER_MODEL_ID
    assert body["choices"][0]["message"]["content"] == "The answer is 42."
    assert body["usage"]["prompt_tokens"] == 100
    assert body["usage"]["completion_tokens"] == 10


@pytest.mark.asyncio
async def test_chat_completions_coding_provider_streaming(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)
    fastapi_app.dependency_overrides[get_mcp_registry] = lambda: MagicMock(spec=McpRegistry)

    federated = FederatedSearch(
        query_id="q-2",
        query="hi",
        hits=[],
        source_status=[],
        duration_ms=5,
        queries=["hi"],
    )
    final = coding_provider.answer_service.AnswerResult(
        text="hello world",
        model="glm-4.7",
        prompt_tokens=50,
        completion_tokens=5,
    )

    async def fake_stream(*args, **kwargs):
        yield "token", {"text": "hello "}
        yield "token", {"text": "world"}
        yield "done", final

    try:
        with (
            patch.object(coding_provider.planner, "plan_queries", new=AsyncMock(return_value=["hi"])),
            patch.object(coding_provider, "federated_search", new=AsyncMock(return_value=(federated, MagicMock()))),
            patch.object(
                coding_provider.answer_service, "synthesize_stream", side_effect=fake_stream
            ),
        ):
            async with authed_client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "model": coding_provider.CODING_PROVIDER_MODEL_ID,
                    "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            ) as response:
                assert response.status_code == 200
                raw = ""
                async for chunk in response.aiter_text():
                    raw += chunk
    finally:
        fastapi_app.dependency_overrides.pop(get_mcp_registry, None)

    assert 'data: {"id":' in raw
    assert '"content": "hello "' in raw
    assert '"content": "world"' in raw
    assert '"finish_reason": "stop"' in raw
    assert "data: [DONE]\n\n" in raw


# --- cached tokens -----------------------------------------------------------
#
# `SIMULATE_CACHE_HIT` is a marker mock_vllm.py looks for (see
# _wants_cache_hit) - there is no real cache to trigger, so a test opts in by
# name rather than the mock reporting one unconditionally and hiding a
# regression where a real endpoint never sends the field at all.


@pytest.mark.asyncio
async def test_cached_tokens_recorded_non_streaming(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)

    response = await authed_client.post(
        "/v1/chat/completions",
        json={
            "model": "glm-4.7",
            "stream": False,
            "messages": [{"role": "user", "content": "SIMULATE_CACHE_HIT please"}],
        },
        headers={"x-session-affinity": "sess-cache-full"},
    )
    assert response.status_code == 200

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        row = (
            await session.execute(select(LlmRequest).where(LlmRequest.session_id == "sess-cache-full"))
        ).scalar_one()
        assert row.tokens_cached > 0
        # A subset of the prompt, never more of it.
        assert row.tokens_cached <= row.tokens_prompt


@pytest.mark.asyncio
async def test_cached_tokens_recorded_streaming(authed_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)

    async with authed_client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "glm-4.7",
            "stream": True,
            "messages": [{"role": "user", "content": "SIMULATE_CACHE_HIT please"}],
        },
        headers={"x-session-affinity": "sess-cache-stream"},
    ) as response:
        assert response.status_code == 200
        async for _ in response.aiter_text():
            pass

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        row = (
            await session.execute(select(LlmRequest).where(LlmRequest.session_id == "sess-cache-stream"))
        ).scalar_one()
        assert row.tokens_cached > 0
        assert row.tokens_cached <= row.tokens_prompt


@pytest.mark.asyncio
async def test_no_cache_hit_records_zero_not_none(authed_client, test_db_engine) -> None:
    """A provider that never reports the field must not be indistinguishable
    from a crash - the column defaults to 0, not null, and stays 0."""
    await _seed_endpoint(test_db_engine)

    response = await authed_client.post(
        "/v1/chat/completions",
        json={
            "model": "glm-4.7",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-session-affinity": "sess-no-cache"},
    )
    assert response.status_code == 200

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        row = (
            await session.execute(select(LlmRequest).where(LlmRequest.session_id == "sess-no-cache"))
        ).scalar_one()
        assert row.tokens_cached == 0
