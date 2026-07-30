"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "model_endpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=False),
        sa.Column("api_key", sa.String(256), nullable=True),
        sa.Column("model_id", sa.String(256), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("ctx_window", sa.Integer, nullable=False, server_default="32768"),
        sa.Column("price_per_mtok_in", sa.Float, nullable=False, server_default="0"),
        sa.Column("price_per_mtok_out", sa.Float, nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("reasoning_profile", sa.JSON, nullable=False),
        sa.Column("tools_mode", sa.String(16), nullable=False, server_default="json_protocol"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_result", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False, unique=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"], unique=True)

    op.create_table(
        "llm_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id"), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("parent_session_id", sa.String(128), nullable=True),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("endpoint_id", sa.String(36), sa.ForeignKey("model_endpoints.id"), nullable=True),
        sa.Column("reasoning_level", sa.String(16), nullable=False),
        sa.Column("stream", sa.Boolean, nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("error_type", sa.String(128), nullable=True),
        sa.Column("finish_reason", sa.String(32), nullable=True),
        sa.Column("tool_call_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_prompt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_completion", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_reasoning", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("ttft_ms", sa.Integer, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_requests_user_id", "llm_requests", ["user_id"])
    op.create_index("ix_llm_requests_session_id", "llm_requests", ["session_id"])


def downgrade() -> None:
    op.drop_table("llm_requests")
    op.drop_table("api_keys")
    op.drop_table("model_endpoints")
    op.drop_table("users")
