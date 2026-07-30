"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-29

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
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "invites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_invites_code", "invites", ["code"], unique=True)

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
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("workspace_path", sa.String(512), nullable=False),
        sa.Column("file_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "project_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("token_count", sa.Integer, nullable=True),
        sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_files_project_id", "project_files", ["project_id"])

    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_text", sa.Text, nullable=False),
        sa.Column("mode", sa.String(24), nullable=False, server_default="approve_plan"),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("planner_endpoint_id", sa.String(36), sa.ForeignKey("model_endpoints.id"), nullable=False),
        sa.Column("executor_endpoint_id", sa.String(36), sa.ForeignKey("model_endpoints.id"), nullable=False),
        sa.Column("reasoning_level_planner", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("reasoning_level_executor", sa.String(16), nullable=False, server_default="off"),
        sa.Column("plan", sa.JSON, nullable=True),
        sa.Column("current_step_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_prompt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_completion", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_reasoning", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_estimate_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_runs_project_id", "runs", ["project_id"])
    op.create_index("ix_runs_user_id", "runs", ["user_id"])
    op.create_index("ix_runs_status", "runs", ["status"])

    op.create_table(
        "run_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])

    op.create_table(
        "compactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("tokens_before", sa.Integer, nullable=False),
        sa.Column("tokens_after", sa.Integer, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_compactions_run_id", "compactions", ["run_id"])

    op.create_table(
        "user_interventions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_interventions_run_id", "user_interventions", ["run_id"])

    op.create_table(
        "ratings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("thumbs", sa.Boolean, nullable=True),
        sa.Column("plan_score", sa.Integer, nullable=True),
        sa.Column("code_score", sa.Integer, nullable=True),
        sa.Column("instruction_score", sa.Integer, nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ratings_run_id", "ratings", ["run_id"])

    op.create_table(
        "run_outcomes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False, unique=True),
        sa.Column("solved", sa.Boolean, nullable=True),
        sa.Column("tests_passed", sa.Boolean, nullable=True),
        sa.Column("iterations_to_success", sa.Integer, nullable=True),
        sa.Column("user_interventions_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("stop_reason", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("run_outcomes")
    op.drop_table("ratings")
    op.drop_table("user_interventions")
    op.drop_table("compactions")
    op.drop_table("run_events")
    op.drop_table("runs")
    op.drop_table("project_files")
    op.drop_table("projects")
    op.drop_table("model_endpoints")
    op.drop_table("invites")
    op.drop_table("users")
