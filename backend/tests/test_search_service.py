"""Failure isolation, which is the whole reason this layer exists.

The scenario every test here builds on: three sources, one healthy, one
broken, one hung. The search must succeed with the healthy source's results
and say what happened to the other two.
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.source import Source
from app.models.user import User
from app.schemas.search import SearchHit
from app.services.mcp.connector import SearchContext, SourceResult
from app.services.mcp.registry import McpRegistry
from app.services.search.service import federated_search


class FakeConnector:
    """A connector honouring the no-raise contract."""

    kind = "fake"

    def __init__(self, key: str, hits: int = 2):
        self.key = key
        self._hits = hits
        self.calls = 0

    async def health(self):
        return {"ok": True}

    async def search(self, query: str, *, limit: int, ctx: SearchContext) -> SourceResult:
        self.calls += 1
        return SourceResult(
            source_key=self.key,
            hits=[
                SearchHit(id=f"{self.key}:{i}", source=self.key, title=f"{self.key} hit {i}")
                for i in range(self._hits)
            ],
            elapsed_ms=5,
        )


class ReportingFailureConnector(FakeConnector):
    """Fails the way the contract says to: returns the error."""

    async def search(self, query, *, limit, ctx):
        self.calls += 1
        return SourceResult(source_key=self.key, error="upstream refused the connection")


class RaisingConnector(FakeConnector):
    """Breaks the contract by raising. The service must survive it anyway."""

    async def search(self, query, *, limit, ctx):
        self.calls += 1
        raise RuntimeError("connector bug")


class HangingConnector(FakeConnector):
    async def search(self, query, *, limit, ctx):
        self.calls += 1
        await asyncio.sleep(30)
        raise AssertionError("should have been cancelled")


class PartialConnector(FakeConnector):
    """Succeeded in part, like Google searching Drive and Gmail together."""

    async def search(self, query, *, limit, ctx):
        self.calls += 1
        return SourceResult(
            source_key=self.key,
            hits=[SearchHit(id=f"{self.key}:0", source=self.key, title="partial")],
            degraded=True,
            detail={"errors": ["gmail unavailable"]},
        )


@pytest.fixture
def session_maker(test_db_engine):
    return async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)


def settings(**overrides) -> Settings:
    return Settings(
        search_timeout_seconds=overrides.pop("search_timeout_seconds", 0.5),
        search_per_source_limit=10,
        search_total_limit=40,
        **overrides,
    )


async def seed(session_maker, keys: list[str]) -> User:
    async with session_maker() as db:
        user = User(username="searcher")
        db.add(user)
        for key in keys:
            db.add(Source(key=key, kind="fake", display_name=key.title(), config={}))
        await db.commit()
        await db.refresh(user)
        return user


def registry_with(*connectors) -> McpRegistry:
    registry = McpRegistry(settings(), None)  # type: ignore[arg-type]
    for connector in connectors:
        registry.register(connector)
    return registry


async def run(session_maker, user, registry, **kwargs):
    async with session_maker() as db:
        ctx = SearchContext(db=db, user=user, http_client=None)  # type: ignore[arg-type]
        return await federated_search(
            db, user=user, query="anything", registry=registry, ctx=ctx, settings=settings(), **kwargs
        )


async def test_healthy_sources_are_merged(session_maker):
    user = await seed(session_maker, ["a", "b"])
    registry = registry_with(FakeConnector("a"), FakeConnector("b"))

    result, record = await run(session_maker, user, registry)

    assert len(result.hits) == 4
    assert {status.source for status in result.source_status} == {"a", "b"}
    assert all(status.ok for status in result.source_status)
    assert record.hit_count == 4


async def test_one_reported_failure_does_not_empty_the_results(session_maker):
    user = await seed(session_maker, ["good", "bad"])
    registry = registry_with(FakeConnector("good"), ReportingFailureConnector("bad"))

    result, _ = await run(session_maker, user, registry)

    assert len(result.hits) == 2
    assert all(hit.source == "good" for hit in result.hits)
    bad = next(s for s in result.source_status if s.source == "bad")
    assert bad.ok is False
    assert "refused" in bad.error


async def test_a_connector_that_raises_is_contained(session_maker):
    """The no-raise rule is a contract, but a bug in one connector must not
    be able to take out the whole search."""
    user = await seed(session_maker, ["good", "buggy"])
    registry = registry_with(FakeConnector("good"), RaisingConnector("buggy"))

    result, _ = await run(session_maker, user, registry)

    assert len(result.hits) == 2
    buggy = next(s for s in result.source_status if s.source == "buggy")
    assert buggy.ok is False
    assert "connector bug" in buggy.error


async def test_a_hanging_source_is_timed_out_not_waited_on(session_maker):
    user = await seed(session_maker, ["good", "slow"])
    registry = registry_with(FakeConnector("good"), HangingConnector("slow"))

    result, _ = await run(session_maker, user, registry)

    assert len(result.hits) == 2
    slow = next(s for s in result.source_status if s.source == "slow")
    assert slow.ok is False
    assert "timed out" in slow.error


async def test_every_source_failing_is_still_a_successful_search(session_maker):
    """Zero results with three explained failures is a far better outcome
    than a 500 - the user can see which backends are down."""
    user = await seed(session_maker, ["a", "b", "c"])
    registry = registry_with(
        ReportingFailureConnector("a"), RaisingConnector("b"), HangingConnector("c")
    )

    result, record = await run(session_maker, user, registry)

    assert result.hits == []
    assert len(result.source_status) == 3
    assert all(status.ok is False for status in result.source_status)
    assert all(status.error for status in result.source_status)
    assert record.hit_count == 0


async def test_a_slow_source_does_not_serialise_the_others(session_maker):
    """Fan-out must be concurrent: three sources at ~0.5s each should take
    about 0.5s, not 1.5s."""
    user = await seed(session_maker, ["a", "b", "c"])
    registry = registry_with(HangingConnector("a"), HangingConnector("b"), HangingConnector("c"))

    started = asyncio.get_running_loop().time()
    await run(session_maker, user, registry)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 1.2, f"sources appear to run sequentially ({elapsed:.2f}s)"


async def test_partial_success_is_reported_as_degraded(session_maker):
    user = await seed(session_maker, ["partial"])
    registry = registry_with(PartialConnector("partial"))

    result, _ = await run(session_maker, user, registry)

    status = result.source_status[0]
    assert status.ok is True
    assert status.degraded is True
    assert len(result.hits) == 1


async def test_disabled_sources_are_not_searched(session_maker):
    user = await seed(session_maker, ["on", "off"])
    async with session_maker() as db:
        source = (await db.execute(__import__("sqlalchemy").select(Source).where(Source.key == "off"))).scalar_one()
        source.enabled = False
        await db.commit()

    off = FakeConnector("off")
    registry = registry_with(FakeConnector("on"), off)
    result, _ = await run(session_maker, user, registry)

    assert off.calls == 0
    assert {status.source for status in result.source_status} == {"on"}


async def test_requested_sources_filter_the_fan_out(session_maker):
    user = await seed(session_maker, ["a", "b"])
    b = FakeConnector("b")
    registry = registry_with(FakeConnector("a"), b)

    result, _ = await run(session_maker, user, registry, requested_sources=["a"])

    assert b.calls == 0
    assert {status.source for status in result.source_status} == {"a"}


async def test_a_source_row_with_no_connector_is_skipped(session_maker):
    """A deployment can have a row for a source this build does not
    implement. That is configuration, not a runtime error."""
    user = await seed(session_maker, ["implemented", "not_implemented"])
    registry = registry_with(FakeConnector("implemented"))

    result, _ = await run(session_maker, user, registry)

    assert {status.source for status in result.source_status} == {"implemented"}


async def test_the_search_is_recorded_with_per_source_detail(session_maker):
    user = await seed(session_maker, ["good", "bad"])
    registry = registry_with(FakeConnector("good"), ReportingFailureConnector("bad"))

    _, record = await run(session_maker, user, registry)

    assert record.query == "anything"
    assert set(record.sources) == {"good", "bad"}
    assert record.per_source["good"]["hits"] == 2
    assert record.per_source["bad"]["error"]
    assert record.duration_ms >= 0
