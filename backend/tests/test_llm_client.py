import pytest

from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.services.llm.client import (
    ContentDelta,
    FinishEvent,
    LLMClient,
    ReasoningDelta,
    ToolCallComplete,
    ToolCallDelta,
)
from app.services.llm.toolcalls import ToolSpec

LIST_DIR_TOOL = ToolSpec(name="list_dir", description="List a directory.", parameters={"type": "object", "properties": {}})


def _endpoint(model_id: str, tools_mode: str = "native") -> ModelEndpoint:
    return ModelEndpoint(
        id="ep-1",
        name="test",
        base_url="http://mock-vllm/v1",
        model_id=model_id,
        role="executor",
        tools_mode=tools_mode,
        reasoning_profile=DEFAULT_REASONING_PROFILE,
    )


@pytest.mark.asyncio
async def test_stream_chat_field_mode_reasoning_and_content(mock_vllm_http_client) -> None:
    endpoint = _endpoint("glm-4.7")
    client = LLMClient(endpoint, http_client=mock_vllm_http_client)

    events = [
        event
        async for event in client.stream_chat(
            messages=[{"role": "user", "content": "hi"}], reasoning_level="medium"
        )
    ]

    reasoning_text = "".join(e.text for e in events if isinstance(e, ReasoningDelta))
    content_text = "".join(e.text for e in events if isinstance(e, ContentDelta))

    assert "Analyzing" in reasoning_text
    assert "mock completion" in content_text
    assert any(isinstance(e, FinishEvent) and e.reason == "stop" for e in events)


@pytest.mark.asyncio
async def test_stream_chat_inline_think_model_extracts_reasoning(mock_vllm_http_client) -> None:
    endpoint = _endpoint("glm-4.7-inline-think")
    client = LLMClient(endpoint, http_client=mock_vllm_http_client)

    events = [
        event
        async for event in client.stream_chat(
            messages=[{"role": "user", "content": "hi"}], reasoning_level="high"
        )
    ]

    reasoning_text = "".join(e.text for e in events if isinstance(e, ReasoningDelta))
    content_text = "".join(e.text for e in events if isinstance(e, ContentDelta))

    assert "Analyzing" in reasoning_text
    assert "<think>" not in content_text
    assert "mock completion" in content_text


@pytest.mark.asyncio
async def test_stream_chat_off_level_has_no_reasoning(mock_vllm_http_client) -> None:
    endpoint = _endpoint("glm-4.7-flash")
    client = LLMClient(endpoint, http_client=mock_vllm_http_client)

    events = [
        event
        async for event in client.stream_chat(
            messages=[{"role": "user", "content": "hi"}], reasoning_level="off"
        )
    ]

    assert not any(isinstance(e, ReasoningDelta) for e in events)
    assert any(isinstance(e, ContentDelta) for e in events)


@pytest.mark.asyncio
async def test_stream_chat_native_tool_call_accumulates_across_deltas(mock_vllm_http_client) -> None:
    endpoint = _endpoint("glm-4.7", tools_mode="native")
    client = LLMClient(endpoint, http_client=mock_vllm_http_client)

    events = [
        event
        async for event in client.stream_chat(
            messages=[{"role": "user", "content": "list files"}],
            reasoning_level="off",
            tools=[LIST_DIR_TOOL],
        )
    ]

    deltas = [e for e in events if isinstance(e, ToolCallDelta)]
    completes = [e for e in events if isinstance(e, ToolCallComplete)]

    assert len(deltas) >= 1
    assert len(completes) == 1
    assert completes[0].name == "list_dir"
    assert completes[0].arguments == {"path": "."}
    assert any(isinstance(e, FinishEvent) and e.reason == "tool_calls" for e in events)


@pytest.mark.asyncio
async def test_stream_chat_json_protocol_mode_never_requests_native_tools(mock_vllm_http_client) -> None:
    # tools_mode=json_protocol means the client must not send `tools` at all,
    # so the mock server falls back to its plain-content path even though a
    # tool spec was passed in.
    endpoint = _endpoint("glm-4.7", tools_mode="json_protocol")
    client = LLMClient(endpoint, http_client=mock_vllm_http_client)

    events = [
        event
        async for event in client.stream_chat(
            messages=[{"role": "user", "content": "list files"}],
            reasoning_level="off",
            tools=[LIST_DIR_TOOL],
        )
    ]

    assert not any(isinstance(e, ToolCallComplete) for e in events)
    assert any(isinstance(e, ContentDelta) for e in events)
