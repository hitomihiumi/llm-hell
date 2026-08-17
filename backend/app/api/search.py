"""The search API: one JSON route and one SSE route over the same pipeline.

Both share `federated_search()` and the answer synthesiser; only the
transport differs. The JSON route exists because tests, `curl` and any
client behind a buffering proxy need it, and because "give me the results
without an answer" is a legitimate and much faster request.
"""

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.openai_proxy import get_http_client
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.sessions import CurrentUser, require_csrf
from app.models.endpoint import ModelEndpoint
from app.models.search_query import SearchQuery
from app.models.user import User
from app.schemas.search import (
    AnswerOut,
    SearchQueryOut,
    SearchRequest,
    SearchResponse,
)
from app.services.mcp.connector import SearchContext
from app.services.mcp.registry import McpRegistry, get_mcp_registry
from app.services.search import answer as answer_service
from app.services.search.service import federated_search
from app.services.stats.recorder import RequestOutcome, record_request

logger = logging.getLogger("llmhell.api.search")

router = APIRouter(prefix="/api", tags=["search"], dependencies=[Depends(require_csrf)])


async def _answer_endpoint(db: AsyncSession, settings: Settings) -> ModelEndpoint | None:
    endpoints = list(
        (await db.execute(select(ModelEndpoint).order_by(ModelEndpoint.name))).scalars().all()
    )
    return answer_service.select_endpoint(endpoints, settings)


async def _record_answer_telemetry(
    db: AsyncSession,
    *,
    user: User,
    endpoint: ModelEndpoint | None,
    query_id: str,
    result: answer_service.AnswerResult,
    started_at: float,
) -> None:
    """One llm_requests row per answer call, so the existing Grafana
    dashboards cover the knowledge base too without new panels.

    api_key=None: this user authenticated with a session cookie and has no
    API key at all.
    """
    outcome = RequestOutcome(started_at=started_at)
    outcome.prompt_tokens = result.prompt_tokens
    outcome.completion_tokens = result.completion_tokens
    outcome.reasoning_text = result.reasoning
    if result.error:
        outcome.status_code, outcome.error_type = 502, "answer_failed"
    try:
        await record_request(
            db,
            user=user,
            api_key=None,
            endpoint=endpoint,
            requested_model=(endpoint.model_id if endpoint else ""),
            reasoning_level="default",
            stream=False,
            # Joins this call to the search that triggered it.
            session_id=query_id,
            parent_session_id=None,
            outcome=outcome,
        )
    except Exception:  # noqa: BLE001 - telemetry must never fail a request
        logger.exception("failed to record answer telemetry")


def _to_answer_out(result: answer_service.AnswerResult) -> AnswerOut:
    return AnswerOut(
        text=result.text,
        model=result.model,
        citations=result.citations,
        hits_used=result.hits_used,
        hits_dropped=result.hits_dropped,
        hallucinated_citations=result.hallucinated_citations,
    )


async def _persist_answer(db: AsyncSession, record: SearchQuery, result: answer_service.AnswerResult) -> None:
    record.answer_text = result.text or None
    record.answer_model = result.model
    record.citations = [citation.model_dump() for citation in result.citations]
    record.hallucinated_citations = result.hallucinated_citations
    await db.commit()


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    current: CurrentUser,
    db: AsyncSession = Depends(get_db),
    registry: McpRegistry = Depends(get_mcp_registry),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> SearchResponse:
    settings = get_settings()
    endpoint = await _answer_endpoint(db, settings)
    ctx = SearchContext(
        db=db, user=current, http_client=http_client, answer_endpoint=endpoint, debug=payload.debug
    )

    federated, record = await federated_search(
        db,
        user=current,
        query=payload.query,
        registry=registry,
        ctx=ctx,
        settings=settings,
        requested_sources=payload.sources,
        limit=payload.limit,
    )

    answer_out: AnswerOut | None = None
    if payload.answer:
        started_at = time.monotonic()
        result = await answer_service.synthesize(
            payload.query, federated.hits, endpoint, settings=settings, http_client=http_client
        )
        await _record_answer_telemetry(
            db, user=current, endpoint=endpoint, query_id=record.id, result=result, started_at=started_at
        )
        await _persist_answer(db, record, result)
        # A failed synthesis is reported in the answer body, not as an HTTP
        # error: the hits are still good and the user should see them.
        answer_out = _to_answer_out(result)
        if result.error:
            answer_out.text = answer_out.text or f"(no answer: {result.error})"

    return SearchResponse(
        query_id=federated.query_id,
        query=federated.query,
        hits=federated.hits,
        source_status=federated.source_status,
        answer=answer_out,
        duration_ms=federated.duration_ms,
    )


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@router.post("/search/stream")
async def search_stream(
    payload: SearchRequest,
    current: CurrentUser,
    request: Request,
    db: AsyncSession = Depends(get_db),
    registry: McpRegistry = Depends(get_mcp_registry),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> StreamingResponse:
    settings = get_settings()

    async def events() -> AsyncIterator[bytes]:
        endpoint = await _answer_endpoint(db, settings)
        ctx = SearchContext(
            db=db, user=current, http_client=http_client, answer_endpoint=endpoint, debug=payload.debug
        )

        try:
            federated, record = await federated_search(
                db,
                user=current,
                query=payload.query,
                registry=registry,
                ctx=ctx,
                settings=settings,
                requested_sources=payload.sources,
                limit=payload.limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("federated search failed")
            yield _sse("error", {"message": str(exc), "stage": "search"})
            return

        yield _sse(
            "meta",
            {
                "query_id": federated.query_id,
                "query": federated.query,
                "answer_model": endpoint.model_id if endpoint else None,
            },
        )
        # Emitted BEFORE the model is called. This is the point of streaming
        # here: results paint in about a second while the answer is still
        # being written, instead of the page sitting empty until both are
        # done.
        yield _sse(
            "hits",
            {
                "hits": [hit.model_dump(mode="json") for hit in federated.hits],
                "source_status": [status.model_dump(mode="json") for status in federated.source_status],
                "duration_ms": federated.duration_ms,
            },
        )

        if not payload.answer:
            yield _sse("done", {"duration_ms": federated.duration_ms})
            return

        started_at = time.monotonic()
        final: answer_service.AnswerResult | None = None
        async for kind, data in answer_service.synthesize_stream(
            payload.query, federated.hits, endpoint, settings=settings, http_client=http_client
        ):
            if kind == "done":
                final = data
                break
            if kind == "error":
                yield _sse("error", {**data, "stage": "answer"})
                break
            yield _sse(kind, data)

        if final is not None:
            yield _sse("citations", {"citations": [c.model_dump(mode="json") for c in final.citations]})
            await _record_answer_telemetry(
                db, user=current, endpoint=endpoint, query_id=record.id, result=final, started_at=started_at
            )
            await _persist_answer(db, record, final)
            yield _sse(
                "done",
                {
                    "duration_ms": int((time.monotonic() - started_at) * 1000) + federated.duration_ms,
                    "hits_used": final.hits_used,
                    "hits_dropped": final.hits_dropped,
                    "hallucinated_citations": final.hallucinated_citations,
                    "tokens_prompt": final.prompt_tokens,
                    "tokens_completion": final.completion_tokens,
                },
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # nginx buffers text/event-stream by default, which turns a
            # stream into one delivery at the end.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/search/history", response_model=list[SearchQueryOut])
async def history(
    current: CurrentUser,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
) -> list[SearchQuery]:
    rows = (
        await db.execute(
            select(SearchQuery)
            .where(SearchQuery.user_id == current.id)
            .order_by(SearchQuery.created_at.desc())
            .limit(min(max(limit, 1), 100))
        )
    ).scalars().all()
    return list(rows)
