"""The whole point of this service: an OpenAI-compatible `/v1/models` +
`/v1/chat/completions` that opencode (running the actual agent loop and
tool-calling on the tester's machine) talks to instead of a RunPod vLLM
endpoint directly. Every proxied call is billed/routed/metered here, then
forwarded byte-for-byte to the real endpoint.

Streaming is a **byte-level passthrough**, not a re-serialization: this
service doesn't understand every field a given vLLM build might put on a
chunk, so re-encoding JSON it parsed itself would silently drop whatever
it didn't know to look for and could break opencode. Instead the raw SSE
bytes go straight to the client unmodified, while a copy of the same
bytes is parsed on the side (`ReasoningStreamParser`, already used for the
"check endpoint" probe) purely to produce metrics.
"""

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_keys import CurrentKeyUser
from app.core.config import get_settings
from app.core.db import get_db
from app.models.endpoint import ModelEndpoint
from app.services.llm.coding_provider import CODING_PROVIDER_MODEL_ID
from app.services.llm.coding_provider import chat_completions as coding_chat_completions
from app.services.llm.model_routing import DEFAULT_LEVEL, published_model_ids, resolve_model
from app.services.llm.reasoning import ReasoningStreamParser
from app.services.mcp.registry import McpRegistry, get_mcp_registry
from app.services.stats.recorder import RequestOutcome, extract_session_ids, record_request

router = APIRouter(tags=["proxy"])

_UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Lazily-created, process-wide client (connection pooling across
    requests) - overridden in tests with one bound to the mock server's
    ASGI transport instead of a real socket."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT)
    return _http_client


async def close_http_client() -> None:
    """Called from the app lifespan. Safe to call when no client was ever
    created, and safe to call twice."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


async def _list_enabled_endpoints(db: AsyncSession) -> list[ModelEndpoint]:
    result = await db.execute(
        select(ModelEndpoint).where(ModelEndpoint.enabled.is_(True)).order_by(ModelEndpoint.name)
    )
    return list(result.scalars().all())


@router.get("/v1/models")
async def list_models(_: CurrentKeyUser, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    endpoints = await _list_enabled_endpoints(db)
    created = int(time.time())
    data = [
        {"id": model_id, "object": "model", "created": created, "owned_by": "llmhell"}
        for endpoint in endpoints
        for model_id, _level in published_model_ids(endpoint)
    ]
    # The backend itself can answer coding questions using the same RAG
    # pipeline that powers the web search UI.
    if endpoints:
        data.append({"id": CODING_PROVIDER_MODEL_ID, "object": "model", "created": created, "owned_by": "llmhell"})
    return {"object": "list", "data": data}


def resolve_reasoning_level(body: dict[str, Any]) -> str:
    """The level this request asked for, for recording purposes.

    opencode has its own effort selector (Default/Low/Medium/High/Max) and
    puts the choice in `reasoning_effort`. Reading it here - rather than
    deriving it from the model id, as this used to - is what lets that
    selector actually control anything.
    """
    effort = body.get("reasoning_effort")
    if not isinstance(effort, str) or not effort:
        return DEFAULT_LEVEL
    # The column is String(16); vLLM's own vocabulary fits, but a client is
    # free to send anything and a long value would fail the insert.
    return effort[:16]


def _build_upstream_request_body(body: dict[str, Any], endpoint: ModelEndpoint) -> dict[str, Any]:
    upstream_body = dict(body)
    upstream_body["model"] = endpoint.model_id

    # `reasoning_effort` is passed THROUGH untouched. It used to be
    # overwritten from the model id's suffix, which silently defeated
    # opencode's effort selector: whatever the user picked was replaced.
    #
    # The one exception is an endpoint whose profile has no `levels` at all.
    # That is the escape hatch for a model that misbehaves when asked for an
    # effort level - GLM-4.7 emitted looping garbage for any value - so for
    # those the field is stripped rather than forwarded.
    levels = (endpoint.reasoning_profile or {}).get("levels")
    if not levels:
        upstream_body.pop("reasoning_effort", None)

    if upstream_body.get("stream"):
        stream_options = dict(upstream_body.get("stream_options") or {})
        stream_options["include_usage"] = True
        upstream_body["stream_options"] = stream_options

    return upstream_body


def _upstream_headers(endpoint: ModelEndpoint) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if endpoint.api_key:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
    return headers


async def _stream_upstream_body(
    upstream_response: httpx.Response,
    *,
    reasoning_profile: dict[str, Any],
    outcome: RequestOutcome,
) -> AsyncIterator[bytes]:
    parser = ReasoningStreamParser((reasoning_profile or {}).get("parse", {}))
    buffer = b""
    tool_call_indices: set[int] = set()

    try:
        async for chunk in upstream_response.aiter_bytes():
            yield chunk

            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[len(b"data:") :].strip()
                if payload in (b"", b"[DONE]"):
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                usage = event.get("usage")
                if usage:
                    outcome.prompt_tokens = usage.get("prompt_tokens") or 0
                    outcome.completion_tokens = usage.get("completion_tokens") or 0

                choices = event.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}

                parsed = parser.feed(delta)
                if outcome.ttft_seconds is None and (parsed.content_text or parsed.reasoning_text):
                    outcome.ttft_seconds = time.monotonic() - outcome.started_at
                if parsed.reasoning_text:
                    outcome.reasoning_text += parsed.reasoning_text

                for tool_call_delta in delta.get("tool_calls") or []:
                    tool_call_indices.add(tool_call_delta.get("index", 0))

                finish_reason = choice.get("finish_reason")
                if finish_reason:
                    outcome.finish_reason = finish_reason
    finally:
        outcome.tool_call_count = len(tool_call_indices)
        await upstream_response.aclose()


def _parse_full_response_metrics(body: bytes, outcome: RequestOutcome) -> None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return

    usage = payload.get("usage") or {}
    outcome.prompt_tokens = usage.get("prompt_tokens") or 0
    outcome.completion_tokens = usage.get("completion_tokens") or 0

    choices = payload.get("choices") or []
    if not choices:
        return
    message = choices[0].get("message") or {}
    # Both names, because which one a server uses is version-dependent:
    # vLLM 0.26.0 emits "reasoning", older builds emit "reasoning_content".
    # Reading only one silently reports zero reasoning tokens against the
    # other.
    outcome.reasoning_text = message.get("reasoning") or message.get("reasoning_content") or ""
    outcome.finish_reason = choices[0].get("finish_reason")
    outcome.tool_call_count = len(message.get("tool_calls") or [])
    if outcome.reasoning_text or (message.get("content") or ""):
        outcome.ttft_seconds = time.monotonic() - outcome.started_at


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    current: CurrentKeyUser,
    db: AsyncSession = Depends(get_db),
    http_client: httpx.AsyncClient = Depends(get_http_client),
    registry: McpRegistry = Depends(get_mcp_registry),
) -> Response:
    user, api_key = current

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "invalid JSON body", "type": "invalid_request"}}, status_code=400)

    requested_model = body.get("model")
    stream = bool(body.get("stream", False))
    session_id, parent_session_id = extract_session_ids(
        request.headers, api_key_id=api_key.id, messages=body.get("messages") or []
    )

    if requested_model == CODING_PROVIDER_MODEL_ID:
        return await coding_chat_completions(
            body,
            db=db,
            user=user,
            settings=get_settings(),
            registry=registry,
            http_client=http_client,
        )

    outcome = RequestOutcome()

    async def _fail(status_code: int, error_type: str, message: str) -> Response:
        outcome.status_code, outcome.error_type = status_code, error_type
        await record_request(
            db,
            user=user,
            api_key=api_key,
            endpoint=None,
            requested_model=requested_model or "",
            reasoning_level=resolve_reasoning_level(body),
            stream=stream,
            session_id=session_id,
            parent_session_id=parent_session_id,
            outcome=outcome,
        )
        return JSONResponse({"error": {"message": message, "type": error_type}}, status_code=status_code)

    if not requested_model:
        return await _fail(400, "missing_model", "\"model\" is required")

    endpoints = await _list_enabled_endpoints(db)
    resolved = resolve_model(endpoints, requested_model)
    if resolved is None:
        return await _fail(400, "unknown_model", f"unknown model: {requested_model!r}")

    endpoint, _ = resolved
    # From the request, not the model id: opencode's effort selector is
    # what sets this now.
    reasoning_level = resolve_reasoning_level(body)
    upstream_body = _build_upstream_request_body(body, endpoint)
    upstream_url = endpoint.base_url.rstrip("/") + "/chat/completions"

    async def _record(final: RequestOutcome) -> None:
        await record_request(
            db,
            user=user,
            api_key=api_key,
            endpoint=endpoint,
            requested_model=requested_model,
            reasoning_level=reasoning_level,
            stream=stream,
            session_id=session_id,
            parent_session_id=parent_session_id,
            outcome=final,
        )

    try:
        upstream_request = http_client.build_request(
            "POST", upstream_url, json=upstream_body, headers=_upstream_headers(endpoint)
        )
        upstream_response = await http_client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        outcome.status_code, outcome.error_type = 502, exc.__class__.__name__
        await _record(outcome)
        return JSONResponse(
            {"error": {"message": f"upstream unreachable: {exc}", "type": "upstream_unreachable"}},
            status_code=502,
        )

    outcome.status_code = upstream_response.status_code
    response_content_type = upstream_response.headers.get("content-type", "application/json")

    if upstream_response.status_code >= 400:
        error_body = await upstream_response.aread()
        await upstream_response.aclose()
        outcome.error_type = "upstream_error"
        await _record(outcome)
        return Response(content=error_body, status_code=upstream_response.status_code, media_type=response_content_type)

    if not stream:
        body_bytes = await upstream_response.aread()
        await upstream_response.aclose()
        _parse_full_response_metrics(body_bytes, outcome)
        await _record(outcome)
        return Response(content=body_bytes, status_code=upstream_response.status_code, media_type=response_content_type)

    async def _body_iterator() -> AsyncIterator[bytes]:
        # try/finally rather than a plain trailing await: on an early
        # client disconnect, Starlette closes this generator via
        # GeneratorExit, which skips straight past code after the loop -
        # only a finally block still runs, and recording the (partial)
        # outcome still matters for that case.
        try:
            async for chunk in _stream_upstream_body(
                upstream_response, reasoning_profile=endpoint.reasoning_profile, outcome=outcome
            ):
                yield chunk
        finally:
            await _record(outcome)

    return StreamingResponse(_body_iterator(), status_code=upstream_response.status_code, media_type=response_content_type)
