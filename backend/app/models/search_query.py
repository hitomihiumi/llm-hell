"""One row per federated search, kept for the demo's own history view and
as the join key for answer-synthesis telemetry.

`LlmRequest.session_id` is set to this row's id, so a search and the LLM
call it triggered can be correlated in Grafana without a new dashboard
datasource: the existing `llm_requests` panels already group by session.

`per_source` is where a partial failure is recorded. A search of three
sources where GitLab timed out is still a successful search - the API
returns 200 - so the fact that it was degraded has to live somewhere, and
this is it.
"""

from typing import Any, Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class SearchQuery(Base, TimestampMixin):
    __tablename__ = "search_queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)

    query: Mapped[str] = mapped_column(Text, nullable=False)
    # The source filter as requested, e.g. ["gitlab", "postgres_kb"]. Null
    # meaning "all enabled" is stored explicitly as the resolved list, so a
    # later change to which sources are enabled does not rewrite history.
    sources: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # {"gitlab": {"hits": 7, "ms": 412, "error": null, "degraded": false}, ...}
    # The postgres entry additionally carries {"mode": "llm"|"fallback",
    # "sql": "..."} - which of the two paths produced the results is one of
    # the more interesting things this demo can show.
    per_source: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    answer_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_model: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # Only citations that resolved to a hit actually in the prompt. Indices
    # the model invented are counted separately rather than stored.
    citations: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    hallucinated_citations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
