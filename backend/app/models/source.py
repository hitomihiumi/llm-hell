"""A searchable source, backed by an MCP server.

One row per *searchable surface*, not per MCP server: the Google Workspace
server exposes Drive and Gmail through two different fat tools with
different result shapes and different usefulness, so they are two rows
sharing one `kind`. That is what lets a tester switch off email search
without losing Drive.

The `last_checked_at` / `last_check_result` pair mirrors
`app.models.endpoint.ModelEndpoint`, and for the same reason: what a given
deployment actually supports is not knowable from configuration. A GitLab
instance without advanced search silently has no `search_code` tool, and the
only way to find out is to ask it.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid, utcnow

# Stable identifiers used in API requests, filter chips, and per-source
# telemetry keys. Changing one is a breaking change for saved queries.
SOURCE_GOOGLE_DRIVE = "google_drive"
SOURCE_GOOGLE_MAIL = "google_mail"
SOURCE_GITLAB = "gitlab"
SOURCE_POSTGRES_KB = "postgres_kb"


class Source(Base, TimestampMixin):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    key: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    # Which connector implementation handles it:
    # google_workspace | gitlab | postgres
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Multiplier applied to this source's reciprocal-rank contribution. A
    # source that is authoritative but returns few hits can be lifted above
    # one that returns many mediocre ones without touching the fusion maths.
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # Non-secret per-source configuration: account email, project ids,
    # table whitelist overrides.
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # The NAME of the Settings/env field holding this source's credential -
    # never the credential. Secrets stay in the environment; the row only
    # records which one to read, so a database dump leaks nothing.
    secret_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
