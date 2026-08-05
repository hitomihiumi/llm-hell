from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid, utcnow

# Default reasoning_profile shape written by the admin "check endpoint" flow
# and consumed by app.services.llm.reasoning. The real vLLM behaviour
# (reasoning_content vs inline tags, native tool-calls vs none, whether an
# effort level is honoured) is unknown until probed against the live pod.
#
# `levels` drives BOTH what gets sent upstream and which model ids the
# proxy publishes: one id per level, which is how a tester picks a level
# from opencode (it has no reasoning-effort concept of its own - it only
# picks a model). "off" publishes under the bare model_id; the rest get a
# "-{level}" suffix.
#
# The values are vLLM's, not ours: `reasoning_effort` is validated against
# 'none'/'minimal'/'low'/'medium'/'high'/'xhigh'/'max', and anything else
# comes back as a 400. Hence "off" -> "none" rather than sending "off".
#
# An endpoint whose model misbehaves under reasoning_effort can have its
# `levels` set to {} - the proxy then publishes only the bare model id and
# sends no reasoning field at all. That is not hypothetical: GLM-4.7 was
# found to emit corrupted, looping output whenever reasoning_effort was
# set to any value whatsoever, and this is the escape hatch for that,
# per-endpoint and without a code change.
DEFAULT_REASONING_PROFILE: dict[str, Any] = {
    "levels": {
        "off": {"extra_body": {"reasoning_effort": "none"}},
        "low": {"extra_body": {"reasoning_effort": "low"}},
        "medium": {"extra_body": {"reasoning_effort": "medium"}},
        "high": {"extra_body": {"reasoning_effort": "high"}},
    },
    "parse": {"mode": "auto", "field": "reasoning_content", "tags": ["<think>", "</think>"]},
}


class ModelEndpoint(Base, TimestampMixin):
    __tablename__ = "model_endpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)
    # Free-form label (e.g. "planner", "executor", "fast") - purely
    # descriptive under the proxy, which routes by `model` id, not role.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    ctx_window: Mapped[int] = mapped_column(Integer, default=32768, nullable=False)
    price_per_mtok_in: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    price_per_mtok_out: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reasoning_profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=lambda: DEFAULT_REASONING_PROFILE)
    tools_mode: Mapped[str] = mapped_column(String(16), default="json_protocol", nullable=False)  # native | json_protocol
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
