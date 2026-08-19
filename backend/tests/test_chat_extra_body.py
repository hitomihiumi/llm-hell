"""Provider-specific request fields, and surviving a gateway that refuses them.

`chat_template_kwargs` is how a vLLM server is told not to think on a given
request - it is what stopped DeepSeek burning its whole token budget on
reasoning before emitting any SQL. It is also not part of the OpenAI schema,
so anything sitting in front of the model may validate it away.

The retry exists so that swapping a direct vLLM endpoint for a gateway is a
configuration change rather than an investigation.
"""

import httpx
import pytest

from app.models.endpoint import ModelEndpoint
from app.services.llm import chat

ENDPOINT = ModelEndpoint(
    name="test", base_url="https://gateway.example/api/v1", model_id="deepseek/deepseek-chat"
)

OK = {
    "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_extra_fields_are_sent_when_the_server_accepts_them():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return httpx.Response(200, json=OK)

    result = await chat.complete(
        ENDPOINT,
        [{"role": "user", "content": "hi"}],
        http_client=client(handler),
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )

    assert result.content == "hello"
    assert len(seen) == 1
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_a_400_is_retried_once_without_the_extra_fields():
    """A gateway rejecting a field meant as an optimisation should cost the
    optimisation, not the call."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        bodies.append(body)
        if "chat_template_kwargs" in body:
            return httpx.Response(400, json={"error": {"message": "unknown field"}})
        return httpx.Response(200, json=OK)

    result = await chat.complete(
        ENDPOINT,
        [{"role": "user", "content": "hi"}],
        http_client=client(handler),
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )

    assert result.content == "hello"
    assert len(bodies) == 2
    assert "chat_template_kwargs" not in bodies[1]


@pytest.mark.asyncio
async def test_a_400_without_extra_fields_is_not_retried():
    """Otherwise a genuinely malformed request is sent twice and the error
    the caller sees is the second one."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": {"message": "bad model"}})

    with pytest.raises(chat.ChatError):
        await chat.complete(ENDPOINT, [{"role": "user", "content": "hi"}], http_client=client(handler))

    assert calls == 1


@pytest.mark.asyncio
async def test_a_401_is_not_retried():
    """An expired key is not fixed by sending fewer fields, and retrying an
    auth failure just doubles the log noise."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": {"message": "no credentials"}})

    with pytest.raises(chat.ChatError):
        await chat.complete(
            ENDPOINT,
            [{"role": "user", "content": "hi"}],
            http_client=client(handler),
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )

    assert calls == 1


@pytest.mark.asyncio
async def test_the_api_key_travels_as_a_bearer_token():
    """What a hosted gateway requires, and what a local vLLM server ignores."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json=OK)

    keyed = ModelEndpoint(
        name="router",
        base_url="https://openrouter.ai/api/v1",
        model_id="deepseek/deepseek-chat",
        api_key="sk-or-v1-test",
    )
    await chat.complete(keyed, [{"role": "user", "content": "hi"}], http_client=client(handler))

    assert seen == ["Bearer sk-or-v1-test"]


def test_the_url_is_built_the_way_a_gateway_documents_it():
    """OpenRouter documents `https://openrouter.ai/api/v1` as the base, and
    the route is appended - a trailing slash must not produce a double one."""
    assert (
        chat._url(ModelEndpoint(name="x", base_url="https://openrouter.ai/api/v1", model_id="m"))
        == "https://openrouter.ai/api/v1/chat/completions"
    )
    assert (
        chat._url(ModelEndpoint(name="x", base_url="https://openrouter.ai/api/v1/", model_id="m"))
        == "https://openrouter.ai/api/v1/chat/completions"
    )


def test_reasoning_is_read_under_either_spelling():
    """OpenRouter returns `reasoning` for a thinking model; some vLLM builds
    return `reasoning_content`. Reading one silently loses the other."""
    assert chat._extract_message({"choices": [{"message": {"reasoning": "a"}}]})[1] == "a"
    assert chat._extract_message({"choices": [{"message": {"reasoning_content": "b"}}]})[1] == "b"
