"""Fan out to every enabled source, merge, and record what happened.

The contract this module exists to enforce: **a search where some sources
failed is still a successful search.** It returns 200 with the sources that
answered, and reports the ones that did not. A federated search that goes
all-or-nothing is worse than no federation, because the failure is invisible
- the user just sees fewer results and has no reason to distrust them.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.search_query import SearchQuery
from app.models.source import Source
from app.models.user import User
from app.schemas.search import SearchHit, SourceStatus
from app.services.mcp.connector import Connector, SearchContext, SourceResult
from app.services.mcp.registry import McpRegistry
from app.services.mcp.transport import summarise_exception
from app.services.search.ranking import fuse

logger = logging.getLogger("llmhell.search")


@dataclass
class FederatedSearch:
    """Everything the API and the SSE stream both need."""

    query_id: str
    query: str
    hits: list[SearchHit]
    source_status: list[SourceStatus]
    duration_ms: int


async def list_sources(db: AsyncSession, *, enabled_only: bool = True) -> list[Source]:
    statement = select(Source).order_by(Source.key)
    if enabled_only:
        statement = statement.where(Source.enabled.is_(True))
    return list((await db.execute(statement)).scalars().all())


def _select_connectors(
    registry: McpRegistry, sources: list[Source], requested: list[str] | None
) -> list[tuple[Source, Connector]]:
    """Pair each requested, enabled source with its connector.

    A source row with no registered connector is skipped silently: that is a
    deployment that has a row for something this build does not implement,
    which is a configuration state, not a runtime error.
    """
    wanted = set(requested) if requested else None
    pairs: list[tuple[Source, Connector]] = []
    for source in sources:
        if wanted is not None and source.key not in wanted:
            continue
        connector = registry.get(source.key)
        if connector is None:
            logger.debug("no connector registered for source %r", source.key)
            continue
        pairs.append((source, connector))
    return pairs


async def _run_one(
    source: Source, connector: Connector, query: str, *, limit: int, ctx: SearchContext, timeout: float
) -> SourceResult:
    """Belt and braces around a connector that already promises not to raise.

    The promise is part of the Connector contract, but a bug in one connector
    must not be able to take out the whole search, so the guarantee is
    enforced here as well as declared there.
    """
    started = time.monotonic()
    try:
        return await asyncio.wait_for(connector.search(query, limit=limit, ctx=ctx), timeout=timeout)
    except asyncio.TimeoutError:
        return SourceResult(
            source_key=source.key,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=f"timed out after {timeout:.0f}s",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("connector %r raised, which it should not", source.key)
        return SourceResult(
            source_key=source.key,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=summarise_exception(exc),
        )


async def federated_search(
    db: AsyncSession,
    *,
    user: User,
    query: str,
    registry: McpRegistry,
    ctx: SearchContext,
    settings: Settings,
    requested_sources: list[str] | None = None,
    limit: int | None = None,
) -> tuple[FederatedSearch, SearchQuery]:
    started = time.monotonic()

    sources = await list_sources(db)
    pairs = _select_connectors(registry, sources, requested_sources)

    per_source_limit = settings.search_per_source_limit
    total_limit = limit or settings.search_total_limit

    results: list[SourceResult] = list(
        await asyncio.gather(
            *(
                _run_one(
                    source,
                    connector,
                    query,
                    limit=per_source_limit,
                    ctx=ctx,
                    timeout=settings.search_timeout_seconds,
                )
                for source, connector in pairs
            )
        )
    )

    by_source = {result.source_key: result for result in results}
    weights = {source.key: source.weight for source, _ in pairs}

    hits = fuse(
        {result.source_key: result.hits for result in results},
        weights=weights,
        k=settings.search_rrf_k,
        total_limit=total_limit,
        # No single source may take more than half the list.
        per_source_cap=max(1, total_limit // 2),
    )

    status = [
        SourceStatus(
            source=source.key,
            display_name=source.display_name,
            ok=by_source[source.key].ok,
            degraded=by_source[source.key].degraded,
            hits=len(by_source[source.key].hits),
            elapsed_ms=by_source[source.key].elapsed_ms,
            error=by_source[source.key].error,
            detail=by_source[source.key].detail,
        )
        for source, _ in pairs
        if source.key in by_source
    ]

    duration_ms = int((time.monotonic() - started) * 1000)

    # Persisted before the answer is generated, so the row exists even if
    # synthesis fails - and so LlmRequest.session_id can reference it.
    record = SearchQuery(
        user_id=user.id,
        query=query,
        sources=[source.key for source, _ in pairs],
        hit_count=len(hits),
        per_source={
            entry.source: {
                "hits": entry.hits,
                "ms": entry.elapsed_ms,
                "error": entry.error,
                "degraded": entry.degraded,
                **entry.detail,
            }
            for entry in status
        },
        duration_ms=duration_ms,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return (
        FederatedSearch(
            query_id=record.id,
            query=query,
            hits=hits,
            source_status=status,
            duration_ms=duration_ms,
        ),
        record,
    )
