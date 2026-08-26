"""semantic index: document passages and their embeddings

Every source searched lexically, so a question found a document only when the
two happened to share a word - `search_terms` and the query planner both exist
to paper over that. These rows hold a passage and its vector, so a question
finds it by meaning instead, across languages.

Plain column types on purpose: the test suite builds this schema on SQLite, so
there is no pgvector here. The similarity is computed in Python, which for a
corpus of hundreds of documents costs milliseconds.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        # Byte length of the document the chunk was cut from, so an edit that
        # keeps the id still invalidates the index.
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        # JSON rather than a vector type: this schema also builds on SQLite.
        sa.Column("embedding", sa.JSON(), nullable=False),
        # Stored beside the vector so a model change is detectable rather than
        # silently comparing incomparable numbers.
        sa.Column("dims", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_document_chunks_source_key", "document_chunks", ["source_key"])
    op.create_index("ix_document_chunks_external_id", "document_chunks", ["external_id"])
    op.create_index("ix_document_chunks_model", "document_chunks", ["model"])
    op.create_index(
        "ix_document_chunks_document", "document_chunks", ["source_key", "external_id", "fingerprint"]
    )
    op.create_unique_constraint(
        "uq_document_chunk",
        "document_chunks",
        ["source_key", "external_id", "fingerprint", "chunk_index"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_document_chunk", "document_chunks", type_="unique")
    op.drop_index("ix_document_chunks_document", table_name="document_chunks")
    op.drop_index("ix_document_chunks_model", table_name="document_chunks")
    op.drop_index("ix_document_chunks_external_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_source_key", table_name="document_chunks")
    op.drop_table("document_chunks")
