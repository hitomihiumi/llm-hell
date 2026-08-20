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
from dataclasses import dataclass, field

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
    # Every phrasing that was actually run, the user's first. Surfaced so the
    # interface can show what the search did rather than leaving a rewrite to
    # happen invisibly - a result the user cannot connect to their question
    # reads as a bug even when it is better.
    queries: list[str] = field(default_factory=list)


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
    except TimeoutError:
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



def _merge_attempts(attempts: list[SourceResult]) -> SourceResult:
    """One source's results across every phrasing, as a single result.

    A hit found by two phrasings keeps its BEST rank, because rank within a
    source is what fusion consumes and a document that answered two ways is
    not less relevant than one that answered once.

    A source is failed only if it failed for every phrasing: one query timing
    out while another returned is a degraded source, not a dead one.
    """
    if len(attempts) == 1:
        return attempts[0]

    merged = SourceResult(source_key=attempts[0].source_key)
    best: dict[str, SearchHit] = {}

    for attempt in attempts:
        for hit in attempt.hits:
            existing = best.get(hit.id)
            if existing is None or hit.rank_in_source < existing.rank_in_source:
                best[hit.id] = hit

    merged.hits = sorted(best.values(), key=lambda hit: hit.rank_in_source)
    # Re-numbered so ranks are contiguous again; fusion reads position, and a
    # merged list with holes in it would weight hits by an accident of which
    # phrasing found them.
    for rank, hit in enumerate(merged.hits):
        hit.rank_in_source = rank

    succeeded = [attempt for attempt in attempts if attempt.ok]
    merged.error = None if succeeded else attempts[0].error
    merged.degraded = any(attempt.degraded for attempt in attempts) or (
        bool(succeeded) and len(succeeded) < len(attempts)
    )
    # The whole fan-out ran concurrently, so the cost is the slowest leg.
    merged.elapsed_ms = max(attempt.elapsed_ms for attempt in attempts)
    merged.detail = {
        **(succeeded[0].detail if succeeded else attempts[0].detail),
        "queries_run": len(attempts),
    }
    return merged


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
    queries: list[str] | None = None,
) -> tuple[FederatedSearch, SearchQuery]:
    """`queries` are the phrasings to run; `query` is what the user typed and
    is what gets recorded. See `planner.py` for why there is more than one."""
    started = time.monotonic()

    sources = await list_sources(db)
    pairs = _select_connectors(registry, sources, requested_sources)

    per_source_limit = settings.search_per_source_limit
    total_limit = limit or settings.search_total_limit

    # Every planned phrasing against every source, all at once. Concurrent
    # rather than sequential because the queries are independent: three
    # phrasings cost the latency of the slowest one, not the sum of three.
    plans = queries or [query]
    gathered: list[list[SourceResult]] = []
    for attempt in await asyncio.gather(
        *(
            asyncio.gather(
                *(
                    _run_one(
                        source,
                        connector,
                        plan,
                        limit=per_source_limit,
                        ctx=ctx,
                        timeout=settings.search_timeout_seconds,
                    )
                    for source, connector in pairs
                )
            )
            for plan in plans
        )
    ):
        gathered.append(list(attempt))

    results: list[SourceResult] = [
        _merge_attempts([attempt[index] for attempt in gathered])
        for index in range(len(pairs))
    ]

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
            queries=plans,
        ),
        record,
    )
