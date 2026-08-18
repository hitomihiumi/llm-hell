"""Calling a registered `ModelEndpoint` directly, rather than proxying to it.

`app.api.openai_proxy` forwards a client's request byte-for-byte; this is
the other direction - the service itself acting as the client, for the two
things that need a model of our own: turning a question into SQL, and
writing the cited answer over search results.

Kept separate from `probe.py` (which uses the `openai` package) because
this is a thin, explicit HTTP call whose streaming half has to hand raw
deltas to `ReasoningStreamParser`, and wrapping that in an SDK buys nothing.
"""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.models.endpoint import ModelEndpoint

logger = logging.getLogger("llmhell.chat")


class ChatError(Exception):
    """The endpoint could not be reached, or returned an error."""


@dataclass
class ChatResult:
    content: str = ""
    reasoning: str = ""
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


def _headers(endpoint: ModelEndpoint) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if endpoint.api_key:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
    return headers


def _url(endpoint: ModelEndpoint) -> str:
    return endpoint.base_url.rstrip("/") + "/chat/completions"


def _body(
    endpoint: ModelEndpoint,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
    stream: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": endpoint.model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }
    if stream:
        # Without this the final chunk carries no usage block and token
        # counts for the answer call are silently zero.
        body["stream_options"] = {"include_usage": True}
    # Server-specific fields the caller needs, merged last so a caller can
    # override a default above. Used for `chat_template_kwargs`, which is how
    # a reasoning model is told not to think on a given request.
    if extra:
        body.update(extra)
    return body


def _extract_message(payload: dict[str, Any]) -> tuple[str, str, str | None]:
    choices = payload.get("choices") or []
    if not choices:
        return "", "", None
    choice = choices[0]
    message = choice.get("message") or {}
    # Both spellings: vLLM 0.26 emits "reasoning", older builds
    # "reasoning_content". Reading one silently loses the other.
    reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
    return message.get("content") or "", reasoning, choice.get("finish_reason")


async def complete(
    endpoint: ModelEndpoint,
    messages: list[dict[str, str]],
    *,
    http_client: httpx.AsyncClient,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: float = 120.0,
    extra_body: dict[str, Any] | None = None,
) -> ChatResult:
    """One non-streaming completion."""
    try:
        response = await http_client.post(
            _url(endpoint),
            json=_body(
                endpoint,
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False,
                extra=extra_body,
            ),
            headers=_headers(endpoint),
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise ChatError(f"{endpoint.model_id} unreachable: {exc}") from exc

    if response.status_code >= 400:
        raise ChatError(f"{endpoint.model_id} returned {response.status_code}: {response.text[:300]}")

    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ChatError(f"{endpoint.model_id} returned non-JSON: {response.text[:300]}") from exc

    content, reasoning, finish_reason = _extract_message(payload)
    usage = payload.get("usage") or {}
    return ChatResult(
        content=content,
        reasoning=reasoning,
        finish_reason=finish_reason,
        prompt_tokens=usage.get("prompt_tokens") or 0,
        completion_tokens=usage.get("completion_tokens") or 0,
        raw=payload,
    )


async def stream_deltas(
    endpoint: ModelEndpoint,
    messages: list[dict[str, str]],
    *,
    http_client: httpx.AsyncClient,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: float = 300.0,
) -> AsyncIterator[dict[str, Any]]:
    """Yield each parsed SSE event from a streaming completion.

    Yields the decoded event dicts, not bytes: unlike the proxy - which must
    pass an opaque payload through untouched for a third-party client - the
    consumer here is our own frontend, and it wants reasoning and content as
    separate typed events.
    """
    request = http_client.build_request(
        "POST",
        _url(endpoint),
        json=_body(endpoint, messages, max_tokens=max_tokens, temperature=temperature, stream=True),
        headers=_headers(endpoint),
        timeout=timeout,
    )
    try:
        response = await http_client.send(request, stream=True)
    except httpx.HTTPError as exc:
        raise ChatError(f"{endpoint.model_id} unreachable: {exc}") from exc

    try:
        if response.status_code >= 400:
            body = await response.aread()
            raise ChatError(f"{endpoint.model_id} returned {response.status_code}: {body[:300]!r}")

        buffer = ""
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    # Splitting on "\n" only ever yields complete lines, so
                    # this is genuinely malformed rather than a partial
                    # frame. Skip the event instead of aborting a stream
                    # that is otherwise producing a usable answer.
                    logger.warning("skipping malformed SSE event: %.200s", data)
                    continue
    finally:
        await response.aclose()
