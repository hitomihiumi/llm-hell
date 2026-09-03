"""record how much of the prompt was served from cache

Token usage is a running theme on the ops dashboards - cost, throughput,
context pressure - and the one number missing from `llm_requests` was
whether a request paid full price for its prompt or hit the provider's own
cache. Without it, "prompt tokens" conflates a cheap cache hit with a full
prefill, and the cache-hit-rate panel this migration exists for has no
column to read.

Non-nullable, default 0: 0 both for "nothing was cached" and for "the
upstream never reports the figure" - the two are indistinguishable from
here regardless of column nullability, so a NULL would not buy any more
truth than a 0 does.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_requests",
        sa.Column("tokens_cached", sa.Integer(), nullable=False, server_default="0"),
    )
    # The server_default only exists to backfill existing rows; new inserts
    # always supply the value explicitly, same as every other column here.
    op.alter_column("llm_requests", "tokens_cached", server_default=None)


def downgrade() -> None:
    op.drop_column("llm_requests", "tokens_cached")
