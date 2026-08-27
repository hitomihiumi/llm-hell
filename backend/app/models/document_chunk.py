"""A passage of a document, with the vector that makes it findable by meaning.

Every source this searches is lexical. Drive matches `fullText contains`,
GitLab matches literal code search, the knowledge base matches generated SQL -
so a question only finds a document when the two happen to share a word.
`search_terms` exists to strip a question down to the few words that might
match, and the planner exists to rewrite questions into corpus vocabulary.
Both are compensations for the same missing capability, and neither is
reliable: asked about a gyroscope, the datasheet says `IMU: MPU6000`, and only
a lucky rewrite bridges that.

These rows are that capability. A passage is stored once with its embedding,
and a question finds it by cosine similarity rather than by shared spelling -
across languages too, which matters here because the questions are Ukrainian
and the documents are mostly English.

**Deliberately plain column types**, for the same reason as `document_pages`:
`conftest.py` builds the schema on SQLite, so there is no `pgvector`, no
`ARRAY` and no `JSONB`. The embedding is JSON and the similarity is computed
in Python. For a corpus of hundreds of documents that costs milliseconds; the
upgrade path when it stops being true is a `vector` column and an index in a
migration, and nothing in this file's contract moves.
"""

from typing import Any

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base, TimestampMixin, new_uuid


class DocumentChunk(Base, TimestampMixin):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    # Which source and which document there, so two sources cannot collide on
    # an id and one source can be re-indexed on its own.
    source_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Which page of the document this passage was cut from, when the document
    # has pages at all. This is what lets the answer attach the *matched*
    # page rather than the most decorative one: a chunk is text, and the
    # answer to "which side of the MCU is the USB port on" is a picture.
    #
    # None for anything without pages - a spreadsheet, a source file, a
    # database row - which is most of the corpus.
    page_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Cheap identity for the document's *content*, exactly as document_pages
    # uses it: a file edited in Drive keeps its id, so without this the index
    # would keep answering from a version that no longer exists.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # Enough to build a hit without going back to the source, so a match found
    # only here still produces a card with a working link.
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    url: Mapped[str | None] = mapped_column(Text, nullable=True)

    text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # The vector, as a JSON array of floats. `dims` is stored beside it so a
    # model change is detectable rather than producing silent nonsense: two
    # embeddings of different lengths cannot be compared, and the indexer
    # refuses to mix them.
    embedding: Mapped[list[float]] = mapped_column(JSON, nullable=False, default=list)
    dims: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    # Whatever the crawler wants to keep about where this came from - the
    # GitLab path, the Drive mime type. Not searched, only carried.
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        # One row per chunk of a version of a document. The upsert relies on it.
        UniqueConstraint(
            "source_key", "external_id", "fingerprint", "chunk_index", name="uq_document_chunk"
        ),
        # The lookup the indexer does before deciding whether to embed anything.
        Index("ix_document_chunks_document", "source_key", "external_id", "fingerprint"),
        # Retrieval scans by model, because vectors from two models are not
        # comparable and mixing them would quietly return nonsense.
        Index("ix_document_chunks_model", "model"),
    )
