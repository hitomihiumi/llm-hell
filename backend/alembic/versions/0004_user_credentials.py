"""per-user credentials for Google and GitLab

Until now every credential was deployment-wide and lived in the environment,
and the `sources` table recorded only the NAME of the variable to read. That
cannot hold a token belonging to one of twenty people, so this table does -
encrypted, with a key that is itself an environment variable, so a dump on
its own is still worth nothing. See app/core/crypto.py.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-21

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        # Ciphertext, not a token. Sized for a Google refresh token with room
        # to spare; Fernet output is base64 and about a third longer than what
        # went in.
        sa.Column("secret", sa.String(length=4096), nullable=False),
        sa.Column("account", sa.String(length=256), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Re-authenticating replaces rather than accumulates, so there is
        # never a question of which of three tokens a search used.
        sa.UniqueConstraint("user_id", "provider", name="uq_user_credentials_user_provider"),
    )
    op.create_index("ix_user_credentials_user_id", "user_credentials", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_credentials_user_id", table_name="user_credentials")
    op.drop_table("user_credentials")
