"""The contract every source implements, and the shape a search returns.

The single most important rule here: **`search()` never raises.** It catches
everything and reports the failure inside a `SourceResult`. Federation is
the point of this application, and a federated search where one dead backend
empties the whole result list is worse than no federation at all - so
isolation is structural, guaranteed by each connector, rather than something
the caller has to remember to wrap in a try block. (`service.py` still uses
`return_exceptions=True` as a second layer, for the case where a connector
has a bug.)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.endpoint import ModelEndpoint
from app.models.source import Source
from app.models.user import User
from app.schemas.search import SearchHit


class ConnectorError(Exception):
    """A source could not answer. Carried in SourceResult.error, not raised
    out of search()."""


@dataclass
class SearchContext:
    """Everything a connector might need, passed explicitly rather than
    reached for through module globals - which is what makes connectors
    testable without an app, a database or a network."""

    db: AsyncSession
    user: User
    http_client: httpx.AsyncClient
    # Only the Postgres connector uses this, to turn a question into SQL.
    # None when no endpoint is registered, in which case that connector
    # falls back to a deterministic ILIKE query rather than returning
    # nothing.
    answer_endpoint: ModelEndpoint | None = None
    debug: bool = False


@dataclass
class SourceResult:
    """One source's contribution to a federated search.

    `error` set means the source failed entirely. `degraded` means it
    partially succeeded - the Google connector searches Drive and Gmail in
    one pass, and one of those failing should not discard the other.
    """

    source_key: str
    hits: list[SearchHit] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None
    degraded: bool = False
    # Free-form per-source detail surfaced in the API response and stored on
    # the SearchQuery row: the generated SQL and whether it came from the
    # model or the fallback, which tools were used, and so on.
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


@runtime_checkable
class Connector(Protocol):
    """A searchable source."""

    key: str
    kind: str

    async def health(self) -> dict[str, Any]:
        """Reachability plus what the server can actually do.

        Returns rather than raises, and the result is stored on
        `Source.last_check_result`. This is where "does this GitLab instance
        have search_code at all" gets answered - configuration cannot tell
        us, only the server can.
        """
        ...

    async def search(self, query: str, *, limit: int, ctx: SearchContext) -> SourceResult:
        """Never raises. See the module docstring."""
        ...


def truncate(text: str | None, limit: int) -> str:
    """Snippets are cut to a budget before they ever reach a prompt. An
    ellipsis marks the cut so a truncated snippet is not mistaken for the
    whole document."""
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def parse_timestamp(value: Any) -> datetime | None:
    """Best-effort ISO-8601, returning None instead of raising.

    Timestamps arrive from three different systems in at least four
    formats, and a hit with an unparseable date is still a useful hit - so
    this never fails a result, it just leaves the field empty.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    # Python < 3.11 rejects the trailing "Z"; normalising costs nothing and
    # covers the most common wire format.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
