"""remember which page a semantic chunk came from

A chunk is text, and the answer to a spatial question is a picture. Without
knowing the page, the answer stage could only attach "the most visual pages"
of a document - decided without seeing the question - so on a long document
the prompt got the pages with the most ink rather than the page that matched.

Nullable: most of the corpus has no pages at all.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("page_index", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "page_index")
