"""our own index of transcribed document pages

Drive indexes a PDF's text layer and nothing else, so a term printed only
inside a diagram cannot find its file there. The transcriptions the vision
model produces are stored here and searched locally, which is what makes a
pinout findable - and, since they do not depend on the question, the same
rows are the cache that stops a page being described twice.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_pages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        # Byte length of the file the reading was made from. A document edited
        # in Drive keeps its id, so without this the index would keep matching
        # a version that no longer exists.
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.Text(), nullable=True),
        # Empty means the page was read and had nothing on it - stored so it
        # is not rendered and described again.
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_document_pages_source_key", "document_pages", ["source_key"])
    op.create_index("ix_document_pages_external_id", "document_pages", ["external_id"])
    # The lookup every PDF read does before deciding to render anything.
    op.create_index(
        "ix_document_pages_document", "document_pages", ["source_key", "external_id", "fingerprint"]
    )
    op.create_unique_constraint(
        "uq_document_page", "document_pages", ["source_key", "external_id", "fingerprint", "page_index"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_document_page", "document_pages", type_="unique")
    op.drop_index("ix_document_pages_document", table_name="document_pages")
    op.drop_index("ix_document_pages_external_id", table_name="document_pages")
    op.drop_index("ix_document_pages_source_key", table_name="document_pages")
    op.drop_table("document_pages")
