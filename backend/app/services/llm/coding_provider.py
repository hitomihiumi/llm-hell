"""A synthetic OpenAI-compatible provider backed by the backend's own search.

When opencode asks model `llmhell/coder` a question, the backend does exactly
what the web UI does: it federates the query across Drive, GitLab, Gmail and
the Postgres KB, then synthesises an answer from the hits.  The result is
returned as a normal chat completion, streaming or not.
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.endpoint import ModelEndpoint
from app.models.source import SOURCE_GOOGLE_DRIVE
from app.models.user import User
from app.schemas.search import ChatTurn, SearchHit
from app.services.llm import chat as llm_chat
from app.services.mcp.connector import SearchContext
from app.services.mcp.google import GoogleWorkspaceConnector
from app.services.mcp.registry import McpRegistry
from app.services.search import answer as answer_service
from app.services.search import planner
from app.services.search.service import federated_search

logger = logging.getLogger("llmhell.coding_provider")

CODING_PROVIDER_MODEL_ID = "llmhell/coder"

# A tool call is the only channel this agent has for anything outside the
# open workspace, and that sentence is doing real work: the local tools
# include a terminal, and a terminal can reach GitLab or Google directly -
# git clone, curl, gh, glab - with no credential of its own and no route
# through the tokens the user actually connected. Measured: an agent with a
# working search_knowledge_base tool still tried gh repo view after one
# search came back thin, and the shell command failed anyway because it has
# no GitLab auth. The prompt now says not to before that happens, rather
# than relying on the tool failing usefully when it does.
#
# Built by joining paragraphs on a blank line rather than writing escaped
# newlines directly - note the blank line between every pair of sentences
# below, which is what makes them read as separate paragraphs once the
# model receives this as a system message.
AGENT_SYSTEM_PROMPT = (chr(10) * 2).join(
    [
        "You are a coding assistant working in the user's own editor. You can read and "
        "write files in their workspace, run commands in their terminal, and search the "
        "team's Google Workspace, GitLab and internal knowledge base with "
        "search_knowledge_base.",
        "CRITICAL - language: every sentence you compose yourself (narration, "
        "explanations, summaries, translations of a document you read) must be written in "
        "the same language as the user's own most recent message, held for the entire "
        "reply, start to finish - never switch language partway through. When that "
        "language is Ukrainian, write Ukrainian, and specifically not Russian: Ukrainian "
        "and Russian share a lot of vocabulary, and the wrong default here is Russian, so "
        "check every sentence against that specific mistake before it stands. Concretely, "
        "застосунок not "
        "приложение, "
        "зберігається not "
        "хранится, "
        "середовище not "
        "окружение, "
        "облікові дані not "
        "учётные данные. "
        "A document you read may itself be in English, Russian or anything else - quote a "
        "short passage verbatim if you must, but translate the rest into the user's own "
        "language rather than carrying the source's language into your own sentences, and "
        "when summarising or reproducing most of a document, translate the whole thing "
        "rather than leaving parts of it in the source language.",
        "The terminal has no access to GitLab, Google Drive or Gmail - it cannot see the "
        "user's credentials for them, and a command like git clone, curl, gh or glab aimed "
        "at those services will simply fail. search_knowledge_base is the only way to reach "
        "them, and it already runs as the signed-in user.",
        "search_knowledge_base returns short excerpts, not a document's full text. When one "
        "result looks like the answer but the excerpt is not enough - a README you need in "
        "full, a file whose entire content matters - call read_knowledge_base_result with "
        "that result's id rather than reaching for the terminal.",
        "If a search genuinely finds nothing, say so. Do not fall back to a shell command "
        "aimed at an external service to work around it.",
        "Prefer small, safe steps. Explain what you are doing briefly.",
    ]
)


def _extract_query(messages: list[dict[str, Any]]) -> str | None:
    """The last user message is what we search for."""
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            # A vision-style message is a list of parts; concatenate text parts.
            if isinstance(content, list):
                return " ".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ).strip()
    return None


def _messages_to_history(messages: list[dict[str, Any]]) -> list[ChatTurn]:
    """Convert OpenAI messages to the chat turns answer synthesis understands."""
    history: list[ChatTurn] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant"):
            continue
        if isinstance(content, list):
            text = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            text = content or ""
        history.append(ChatTurn(role=role, content=text))
    # The last user message is the query, not history.
    if history and history[-1].role == "user":
        history.pop()
    return history


async def _select_endpoints(db: AsyncSession, settings: Settings) -> tuple[ModelEndpoint | None, ModelEndpoint | None]:
    endpoints = list((await db.execute(select(ModelEndpoint).order_by(ModelEndpoint.name))).scalars().all())
    return (
        answer_service.select_endpoint(endpoints, settings),
        answer_service.select_vision_endpoint(endpoints, settings),
    )


async def _select_agent_endpoint(db: AsyncSession, settings: Settings) -> ModelEndpoint | None:
    """The endpoint that powers the coding agent, optionally pinned separately."""
    endpoints = list((await db.execute(select(ModelEndpoint).order_by(ModelEndpoint.name))).scalars().all())
    enabled = [endpoint for endpoint in endpoints if endpoint.enabled]
    if not enabled:
        return None
    if settings.agent_model_id:
        for endpoint in enabled:
            if endpoint.model_id == settings.agent_model_id:
                return endpoint
        logger.warning(
            "AGENT_MODEL_ID=%r matches no enabled endpoint; using answer endpoint",
            settings.agent_model_id,
        )
    return answer_service.select_endpoint(endpoints, settings)


async def _page_images(
    hits: list[SearchHit], registry: McpRegistry, settings: Settings
) -> dict[str, list[bytes]]:
    """Same logic as the web search endpoint: attach preview images for hits."""
    if not settings.answer_image_hits:
        return {}

    connector = registry.get(SOURCE_GOOGLE_DRIVE)
    if not isinstance(connector, GoogleWorkspaceConnector):
        return {}

    images: dict[str, list[bytes]] = {}
    for hit in hits:
        if len(images) >= settings.answer_image_hits:
            break
        if hit.source != SOURCE_GOOGLE_DRIVE:
            continue
        try:
            pages = await connector.page_images(hit.id)
        except Exception as exc:  # noqa: BLE001 - pictures are a bonus
            logger.warning("could not render pages of %s: %s", hit.id, exc)
            continue
        if pages:
            images[hit.id] = pages
    return images


def _openai_chunk(chunk_id: str, model: str, delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
    payload: dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _stream_answer(
    query: str,
    hits: list[SearchHit],
    endpoint: ModelEndpoint,
    chunk_id: str,
    model: str,
    settings: Settings,
    http_client: httpx.AsyncClient,
    history: list[ChatTurn],
    images: dict[str, list[bytes]],
) -> AsyncIterator[bytes]:
    """Yield OpenAI SSE chunks from the backend's answer stream."""
    yield _openai_chunk(chunk_id, model, {"role": "assistant"})

    final: answer_service.AnswerResult | None = None
    async for kind, data in answer_service.synthesize_stream(
        query,
        hits,
        endpoint,
        settings=settings,
        http_client=http_client,
        history=history,
        images=images,
    ):
        if kind == "token":
            yield _openai_chunk(chunk_id, model, {"content": data["text"]})
        elif kind == "done":
            final = data
        elif kind == "error":
            yield _openai_chunk(chunk_id, model, {"content": f"\n\n(answer failed: {data.get('message', 'unknown')})"})

    usage: dict[str, Any] | None = None
    if final is not None:
        usage = {
            "prompt_tokens": final.prompt_tokens,
            "completion_tokens": final.completion_tokens,
            "total_tokens": final.prompt_tokens + final.completion_tokens,
        }
    yield _openai_chunk(chunk_id, model, {}, finish_reason="stop")
    if usage:
        yield f"data: {json.dumps({'usage': usage})}\n\n".encode()
    yield b"data: [DONE]\n\n"


async def chat_completions(
    body: dict[str, Any],
    *,
    db: AsyncSession,
    user: User,
    settings: Settings,
    registry: McpRegistry,
    http_client: httpx.AsyncClient,
) -> Response:
    """Handle a chat completion request for `llmhell/coder`."""
    messages = body.get("messages") or []

    # Tool mode: act as a coding agent backed directly by the LLM. The
    # extension executes the tools locally and feeds the results back.
    if body.get("tools"):
        return await agent_chat(body, db=db, settings=settings, http_client=http_client)

    query = _extract_query(messages)
    if not query:
        return JSONResponse(
            {"error": {"message": "no user message found", "type": "invalid_request"}},
            status_code=400,
        )

    endpoint, vision = await _select_endpoints(db, settings)
    if endpoint is None:
        return JSONResponse(
            {"error": {"message": "no answer model endpoint is registered", "type": "service_unavailable"}},
            status_code=503,
        )

    history = _messages_to_history(messages)
    stream = bool(body.get("stream", False))
    chunk_id = f"chatcmpl-{uuid.uuid4().hex}"

    try:
        queries = await planner.plan_queries(
            query,
            history=history,
            endpoint=endpoint,
            http_client=http_client,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("query planning failed for coding provider")
        return JSONResponse(
            {"error": {"message": f"query planning failed: {exc}", "type": "internal_error"}},
            status_code=500,
        )

    ctx = SearchContext(
        db=db,
        user=user,
        http_client=http_client,
        answer_endpoint=endpoint,
        vision_endpoint=vision,
        debug=False,
    )

    try:
        federated, _record = await federated_search(
            db,
            user=user,
            query=query,
            registry=registry,
            ctx=ctx,
            settings=settings,
            queries=queries,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("federated search failed for coding provider")
        return JSONResponse(
            {"error": {"message": f"search failed: {exc}", "type": "internal_error"}},
            status_code=500,
        )

    images = await _page_images(federated.hits, registry, settings)

    if stream:
        return StreamingResponse(
            _stream_answer(query, federated.hits, endpoint, chunk_id, CODING_PROVIDER_MODEL_ID, settings, http_client, history, images),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = await answer_service.synthesize(
        query,
        federated.hits,
        endpoint,
        settings=settings,
        http_client=http_client,
        history=history,
        images=images,
    )

    text = result.text or ""
    if result.error:
        text += f"\n\n(answer failed: {result.error})"

    payload: dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": CODING_PROVIDER_MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.prompt_tokens + result.completion_tokens,
        },
    }
    return JSONResponse(payload)


def _prepare_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepend the agent system prompt when there is no system message."""
    if messages and messages[0].get("role") == "system":
        return messages
    return [{"role": "system", "content": AGENT_SYSTEM_PROMPT}, *messages]


def _openai_completion_response(result: llm_chat.ChatResult, model: str) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": result.content}
    if result.tool_calls:
        message["tool_calls"] = result.tool_calls
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": result.finish_reason or ("tool_calls" if result.tool_calls else "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.prompt_tokens + result.completion_tokens,
        },
    }


async def _stream_agent(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> AsyncIterator[bytes]:
    """Pass through the upstream streaming deltas as OpenAI SSE."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
    async for event in llm_chat.stream_deltas(
        endpoint,
        messages,
        http_client=http_client,
        max_tokens=settings.agent_max_output_tokens,
        temperature=settings.agent_temperature,
        tools=tools,
    ):
        event.setdefault("id", chunk_id)
        if "model" in event:
            event["model"] = CODING_PROVIDER_MODEL_ID
        yield f"data: {json.dumps(event)}\n\n".encode()
    yield b"data: [DONE]\n\n"


async def agent_chat(
    body: dict[str, Any],
    *,
    db: AsyncSession,
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> Response:
    """Tool-enabled coding agent: direct LLM call, no search pipeline."""
    messages = _prepare_messages(body.get("messages") or [])
    tools = body.get("tools") or []
    if not tools:
        return JSONResponse(
            {"error": {"message": "tools are required for agent chat", "type": "invalid_request"}},
            status_code=400,
        )

    endpoint = await _select_agent_endpoint(db, settings)
    if endpoint is None:
        return JSONResponse(
            {"error": {"message": "no answer model endpoint is registered", "type": "service_unavailable"}},
            status_code=503,
        )

    stream = bool(body.get("stream", False))
    if stream:
        return StreamingResponse(
            _stream_agent(endpoint, messages, tools, settings, http_client),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        result = await llm_chat.complete(
            endpoint,
            messages,
            http_client=http_client,
            max_tokens=settings.agent_max_output_tokens,
            temperature=settings.agent_temperature,
            tools=tools,
        )
    except llm_chat.ChatError as exc:
        logger.exception("agent chat failed")
        return JSONResponse(
            {"error": {"message": str(exc), "type": "upstream_error"}},
            status_code=502,
        )

    return JSONResponse(_openai_completion_response(result, CODING_PROVIDER_MODEL_ID))
