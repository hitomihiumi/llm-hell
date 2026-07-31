from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid, utcnow

# Default reasoning_profile shape written by the admin "check endpoint" flow
# and consumed by app.services.llm.reasoning. See the plan doc for the
# rationale: the real vLLM behaviour (reasoning_content vs inline tags,
# native tool-calls vs none) is unknown until probed against the live pod.
#
# There is deliberately no per-level `reasoning_effort` request control
# here anymore: GLM-4.7 (this project's actual target model) was found to
# emit corrupted/looping output whenever `reasoning_effort` was set to
# anything at all, regardless of value, on the vLLM build in use. The
# proxy never requests a reasoning level - `parse` only describes how to
# read back whatever reasoning a model emits on its own.
DEFAULT_REASONING_PROFILE: dict[str, Any] = {
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
