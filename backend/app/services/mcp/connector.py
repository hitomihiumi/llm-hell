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

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.endpoint import ModelEndpoint
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


# Terms shorter than this are ignored when locating the match. Two-letter
# fragments hit almost everywhere and would anchor the excerpt at random.
_MIN_TERM_LENGTH = 3
# How much of the window to spend on lead-in, so the match is a little past
# the start rather than flush against it and easy to miss.
_LEAD_IN_CHARS = 70


def _query_terms(query: str) -> list[str]:
    seen: list[str] = []
    for raw in re.split(r"\W+", (query or "").lower()):
        if len(raw) >= _MIN_TERM_LENGTH and raw not in seen:
            seen.append(raw)
    return seen


# Words that appear in the question and never in the document. Kept lexical
# rather than clever - this is a term filter, not a language model - and
# deliberately short: over-filtering loses the word that identifies the
# search, which is worse than passing one filler word through.
STOPWORDS = frozenset(
    [
        # articles, conjunctions, prepositions
        "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at",
        "with", "from", "that", "this", "it", "about", "into",
        # copulas and auxiliaries
        "is", "are", "was", "were", "does", "do", "can", "should", "would",
        # question words, which begin most of what a person types
        "what", "where", "when", "how", "why", "who", "which",
        # verbs of asking - present in the question, absent from the answer
        "show", "find", "search", "list", "tell", "give", "please", "look",
        "me", "my", "our", "your", "using", "named", "called",
    ]
)

DEFAULT_MAX_TERMS = 4


def search_terms(query: str, *, limit: int = DEFAULT_MAX_TERMS) -> list[str]:
    """The words in a question worth sending to a search backend.

    Every source this talks to treats its query as a literal string, so a
    whole sentence finds nothing at all. `auth-service` returned two GitLab
    hits while `auth-service readme` returned zero, and a Drive document
    plainly titled TEST did not match `fullText contains 'test document'`.
    In both cases the source looked empty when it was merely being asked a
    question no backend could match.

    Ordered longest first, because the word that identifies a search is
    rarely a short one, and capped because a long question is mostly filler
    and every extra term costs either query length or a round-trip.

    Original casing is preserved - some backends match case-sensitively on
    identifiers - while the stopword test is case-insensitive.
    """
    words = re.findall(r"[\w'-]{2,}", query or "", re.UNICODE)
    kept = [word for word in dict.fromkeys(words) if word.lower() not in STOPWORDS]
    return sorted(kept, key=len, reverse=True)[:limit]


def _snap_forward(text: str, index: int) -> int:
    """Move to the next word boundary so an excerpt does not begin mid-word."""
    if index <= 0:
        return 0
    space = text.find(" ", index)
    return index if space == -1 else space + 1


def _snap_back(text: str, index: int) -> int:
    if index >= len(text):
        return len(text)
    space = text.rfind(" ", 0, index)
    return index if space == -1 else space


def excerpt_around(text: str | None, query: str, limit: int) -> str:
    """A window of `text` centred on where the query actually matched.

    Search results from Drive and Postgres carry no match highlight - Drive
    returns no snippet at all, and a KB row hands back a whole column - so
    without this the excerpt is simply the opening of the document. For
    anything longer than a paragraph that means the reader is shown text that
    has nothing to do with why the hit was returned, and a citation invites
    them to go and look for the relevant part themselves.

    Windows are scored by how many DISTINCT query terms fall inside them, not
    by raw occurrence count, so a document that repeats one word a hundred
    times does not outrank the passage where the whole phrase appears.

    Falls back to `truncate` when nothing matches, so this is never worse
    than showing the opening: a hit whose terms live in the title or in
    metadata rather than the body still gets its old excerpt.
    """
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed

    terms = _query_terms(query)
    if not terms:
        return truncate(collapsed, limit)

    lowered = collapsed.lower()
    # Every position where any term appears becomes a candidate anchor.
    anchors = sorted(
        {match.start() for term in terms for match in re.finditer(re.escape(term), lowered)}
    )
    if not anchors:
        return truncate(collapsed, limit)

    # Rank by distinct terms covered, then by how TIGHTLY they cluster.
    #
    # The tie-break is what makes this useful rather than merely correct. A
    # document that repeats one term before the real passage produces dozens
    # of anchors that all technically reach the passage at the far edge of
    # their window; taking the earliest of those opens the excerpt in the
    # noise and pushes the actual match to the last line. Preferring the
    # tightest span puts the reader on the passage itself.
    best_anchor, best_key = anchors[0], (-1, 0)
    for anchor in anchors:
        window_end = anchor + limit
        found = [
            position
            for position in (lowered.find(term, anchor, window_end) for term in terms)
            if position != -1
        ]
        if not found:
            continue
        key = (len(found), -(max(found) - anchor))
        if key > best_key:
            best_anchor, best_key = anchor, key

    start = _snap_forward(collapsed, max(0, best_anchor - _LEAD_IN_CHARS))
    end = _snap_back(collapsed, min(len(collapsed), start + limit))
    if end <= start:  # pathological input, e.g. one enormous "word"
        return truncate(collapsed, limit)

    window = collapsed[start:end].strip()
    # Ellipses on whichever side was actually cut, so the reader can tell the
    # excerpt is an interior slice rather than the beginning of the document.
    return f"{'…' if start > 0 else ''}{window}{'…' if end < len(collapsed) else ''}"


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
