"""The normalised shape a Drive document, a GitLab code hit and a Postgres
row all collapse into, plus the request/response envelopes around it."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

HitKind = Literal["document", "email", "code", "repository", "commit", "row", "unknown"]


class SearchHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Unique within one response only - "gitlab:code:3". The frontend uses
    # it to link a citation to a card; it is not a durable identifier.
    id: str
    source: str
    kind: HitKind = "unknown"
    external_id: str | None = None

    title: str
    snippet: str = ""
    # None when the source genuinely has no addressable location for the
    # hit. Rendered as a non-link rather than a dead link.
    url: str | None = None
    author: str | None = None
    timestamp: datetime | None = None

    # How many pages of this hit can be rendered as pictures, or None when
    # none can. Set by the source that knows - only a PDF has pages - and
    # read by the UI to decide whether to show a preview at all, rather than
    # firing a request per card to find out.
    #
    # These are the pages that were actually used: the ones chosen for the
    # answer prompt, not every page of the file. A preview of "the data used"
    # that quietly showed something else would be worse than none.
    preview_pages: int | None = None

    # Position within its own source's result list, 0-based. This is the
    # only ranking signal that means the same thing across sources, and it
    # is what rank fusion consumes.
    rank_in_source: int = 0
    # Fused score, comparable across sources. Assigned by ranking.py.
    score: float = 0.0
    # The source's own relevance number, when it gives one. Kept for display
    # and debugging, and deliberately NOT used for ordering: these are on
    # unrelated scales and two of the three sources omit it entirely.
    source_score: float | None = None

    # The verbatim MCP payload for this hit. Populated only when the request
    # asks for it, since it can be large and is unfiltered source data.
    raw: dict[str, Any] | None = None


class SourceStatus(BaseModel):
    """Per-source outcome, always returned - including for sources that
    failed. A search that quietly shows fewer results because a backend is
    down is worse than one that says so."""

    source: str
    display_name: str = ""
    ok: bool = True
    degraded: bool = False
    hits: int = 0
    elapsed_ms: int = 0
    error: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    """A `[n]` reference in the answer that resolved to a hit which was
    actually in the prompt. References the model invented are dropped rather
    than rendered, so every citation the UI shows is real by construction."""

    n: int
    hit_id: str
    title: str
    url: str | None = None
    source: str


class ChatTurn(BaseModel):
    """One prior exchange, for follow-up questions in the chat layout."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    # None means "every enabled source".
    sources: list[str] | None = None
    limit: int | None = Field(default=None, ge=1, le=100)
    # Off gives just the federated result list, which is fast and needs no
    # LLM at all.
    answer: bool = True
    debug: bool = False

    # Prior turns, oldest first. Sent by the chat layout so a follow-up like
    # "what about the second one" has something to refer back to.
    #
    # NOTE these inform the ANSWER only - the search itself still runs on
    # `query` alone. Searching for the literal text of a follow-up would be
    # worse than searching for nothing, and rewriting the query from history
    # is a bigger piece of work than this demo needs. The practical effect is
    # that a follow-up re-searches on its own words and the model answers
    # with the conversation in view.
    history: list[ChatTurn] | None = Field(default=None, max_length=20)


class AnswerOut(BaseModel):
    text: str
    model: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    # How many hits fitted in the context window, and how many were dropped
    # for lack of room - so the UI can say "answered from 12 of 31 results"
    # rather than implying it read everything.
    hits_used: int = 0
    hits_dropped: int = 0
    hallucinated_citations: int = 0


class SearchResponse(BaseModel):
    query_id: str
    query: str
    # Every phrasing that was run, the user's own first. The model writes
    # the rest; showing them is what makes a rewritten search legible instead
    # of mysterious.
    queries: list[str] = []
    hits: list[SearchHit] = Field(default_factory=list)
    source_status: list[SourceStatus] = Field(default_factory=list)
    answer: AnswerOut | None = None
    duration_ms: int = 0


class SearchQueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    query: str
    sources: list[str] = Field(default_factory=list)
    hit_count: int = 0
    answer_text: str | None = None
    answer_model: str | None = None
    duration_ms: int = 0
    created_at: datetime
