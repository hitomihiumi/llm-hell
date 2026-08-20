"""The federated search fan-out must not share the request's DB session.

A connector that touches the database (Google Drive's page-index lookup) runs
concurrently with other sources and with other planned phrasings.  Sharing the
request session either races the session itself or leaves the request
transaction in a failed state when a source times out, which breaks the commit
that persists the search record.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.document_page import DocumentPage
from app.models.source import Source
from app.models.user import User
from app.schemas.search import SearchHit
from app.services.mcp.connector import SearchContext, SourceResult
from app.services.mcp.registry import McpRegistry
from app.services.search import page_index
from app.services.search.service import federated_search


class DbTouchingConnector:
    """A connector that reads from the page index the way Google Drive does."""

    kind = "fake"

    def __init__(self, key: str):
        self.key = key
        self.calls = 0

    async def health(self):
        return {"ok": True}

    async def search(self, query: str, *, limit: int, ctx: SearchContext) -> SourceResult:
        self.calls += 1
        await page_index.search_pages(ctx.db, query, source_key="google_drive", limit=limit)
        return SourceResult(
            source_key=self.key,
            hits=[SearchHit(id=f"{self.key}:0", source=self.key, title=f"{self.key} hit")],
            elapsed_ms=1,
        )


@pytest.fixture
def session_maker(test_db_engine):
    return async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)


def _settings(**overrides) -> Settings:
    return Settings(
        search_timeout_seconds=overrides.pop("search_timeout_seconds", 5),
        search_per_source_limit=10,
        search_total_limit=40,
        **overrides,
    )


async def test_db_touching_sources_do_not_break_the_request_session(session_maker):
    async with session_maker() as db:
        user = User(username="searcher")
        db.add(user)
        db.add(Source(key="google_drive", kind="fake", display_name="Drive", config={}))
        db.add(
            DocumentPage(
                source_key="google_drive",
                external_id="file-1",
                page_index=0,
                fingerprint="abc",
                title="file",
                url=None,
                text="hello world",
            )
        )
        await db.commit()
        await db.refresh(user)

    registry = McpRegistry(_settings(), None)
    registry.register(DbTouchingConnector("google_drive"))

    async with session_maker() as db:
        ctx = SearchContext(db=db, user=user, http_client=None)  # type: ignore[arg-type]
        result, record = await federated_search(
            db,
            user=user,
            query="hello",
            registry=registry,
            ctx=ctx,
            settings=_settings(),
            queries=["hello", "world"],
        )

    assert len(result.hits) == 1
    assert record.hit_count == 1
    assert record.query == "hello"
