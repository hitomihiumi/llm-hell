"""knowledge base schema: web sessions, sources, search history

Stacked on 0001 rather than replacing it. 0001 is already applied against
the live volume and `llm_requests` holds telemetry the Grafana dashboards
query, so rewriting it would mean dropping the database - and would leave
anyone already stamped at 0001 in an unresolvable state.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users: web login ---------------------------------------------------
    # All nullable, so existing API-key-only users stay valid rows and no
    # server_default backfill is needed.
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(128), nullable=True))

    # --- llm_requests: answer-synthesis calls have no API key ---------------
    op.alter_column("llm_requests", "api_key_id", existing_type=sa.String(36), nullable=True)

    # --- user_sessions ------------------------------------------------------
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        # sha256 hex digest, not argon2 - see app/models/session.py.
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(256), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"], unique=True)

    # --- sources ------------------------------------------------------------
    op.create_table(
        "sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("weight", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("config", sa.JSON, nullable=False),
        # The NAME of the env field holding the credential, never the value.
        sa.Column("secret_ref", sa.String(128), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_result", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sources_key", "sources", ["key"], unique=True)

    # --- search_queries -----------------------------------------------------
    op.create_table(
        "search_queries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("sources", sa.JSON, nullable=False),
        sa.Column("hit_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("per_source", sa.JSON, nullable=False),
        sa.Column("answer_text", sa.Text, nullable=True),
        sa.Column("answer_model", sa.String(256), nullable=True),
        sa.Column("citations", sa.JSON, nullable=True),
        sa.Column("hallucinated_citations", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_search_queries_user_id", "search_queries", ["user_id"])
    op.create_index("ix_search_queries_created_at", "search_queries", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_search_queries_created_at", table_name="search_queries")
    op.drop_index("ix_search_queries_user_id", table_name="search_queries")
    op.drop_table("search_queries")

    op.drop_index("ix_sources_key", table_name="sources")
    op.drop_table("sources")

    op.drop_index("ix_user_sessions_token_hash", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")

    # Rows written by the web app have a NULL api_key_id and cannot satisfy
    # the restored NOT NULL, so they are removed first. They are telemetry,
    # not user data, and this only runs on a deliberate downgrade.
    op.execute("DELETE FROM llm_requests WHERE api_key_id IS NULL")
    op.alter_column("llm_requests", "api_key_id", existing_type=sa.String(36), nullable=False)

    op.drop_column("users", "display_name")
    op.drop_column("users", "email")
    op.drop_column("users", "password_hash")
