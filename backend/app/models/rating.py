from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class Rating(Base, TimestampMixin):
    __tablename__ = "ratings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    thumbs: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    plan_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1-5
    code_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1-5
    instruction_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1-5
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class RunOutcome(Base, TimestampMixin):
    __tablename__ = "run_outcomes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), unique=True, nullable=False)
    solved: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    tests_passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    iterations_to_success: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    user_interventions_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stop_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
