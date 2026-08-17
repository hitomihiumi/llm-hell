"""One row per proxied `/v1/chat/completions` call. The agent loop now
runs inside opencode on the tester's machine, entirely out of this
service's view - this table (plus the Prometheus histograms/counters in
`services.stats.prom`) is the only technical telemetry the proxy can
still observe.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class LlmRequest(Base):
    __tablename__ = "llm_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    # Nullable since the knowledge-base UI arrived: answer-synthesis calls
    # are made on behalf of a cookie-session user, who has no API key at
    # all. Minting a hidden "web" ApiKey row just to satisfy a NOT NULL
    # would put a credential in the database that nobody can use and that
    # `list-keys` would then display.
    api_key_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("api_keys.id"), nullable=True)

    # Correlates requests belonging to one opencode conversation - from the
    # `x-session-affinity` header opencode sends by default, or a fallback
    # hash when that header is absent (see services.stats.recorder). For
    # answer-synthesis calls it is the SearchQuery.id instead, which is what
    # joins a search to the LLM call it triggered.
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    parent_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    model: Mapped[str] = mapped_column(String(256), nullable=False)
    # Nullable: a request that fails before routing (unknown model id) still
    # gets a row, for the errors dashboard, with no endpoint to attach it to.
    endpoint_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("model_endpoints.id"), nullable=True)
    reasoning_level: Mapped[str] = mapped_column(String(16), nullable=False)
    stream: Mapped[bool] = mapped_column(Boolean, nullable=False)

    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    tokens_prompt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_completion: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_reasoning: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
