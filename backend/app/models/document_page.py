"""One page of a document, as text we produced rather than text a source gave us.

This exists because of a limit that cannot be fixed at the source. Drive
indexes a PDF's **text layer**, so a term printed only inside a diagram - a pad
name, a value on a chart, anything in a scan - will not find the file. Asking
Drive for `UART3` returned nothing while the file sat there with `UART3`
legible on page three.

So the transcriptions the vision model produces are kept here and searched
locally, alongside the source's own search. A page transcribed once is
findable forever, which turns the vision model from a per-search cost into an
index that fills in as documents are read.

The same rows are the cache. A transcription does not depend on the question,
so re-describing a page for every search would be paying a GPU to produce a
string we already have - and an in-process dictionary loses that on every
deploy.

Deliberately plain column types. `conftest.py` builds the schema on SQLite, so
`JSONB`, `ARRAY`, `UUID` and `TSVECTOR` are all unavailable here; matching is
done with the same term reduction the sources use rather than with a
`tsvector` column. For this corpus - hundreds of pages, not millions - that is
the right trade, and the upgrade path is an expression index in a migration
rather than a column type in this file.
"""

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class DocumentPage(Base, TimestampMixin):
    __tablename__ = "document_pages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    # Which source and which document there, so an id collision between two
    # sources is impossible and a source can be re-indexed on its own.
    source_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    page_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # Cheap identity for the file's *content*. A document edited in Drive keeps
    # its id, so without this the index would answer from a reading of a
    # version that no longer exists. Byte length is not a hash, and does not
    # need to be: it costs nothing, and the failure it prevents - an edit that
    # changes the length, which is nearly all of them - is the common one.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # Enough to build a hit without going back to the source, so a search that
    # matches only the local index still produces a card with a working link.
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Empty means "this page was read and had nothing worth keeping" - a
    # blank or purely decorative page. Stored rather than skipped, so it is
    # not rendered and described again on the next search.
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        # One row per page of a version. The upsert relies on this.
        UniqueConstraint(
            "source_key", "external_id", "fingerprint", "page_index", name="uq_document_page"
        ),
        # The lookup the cache does on every PDF read.
        Index("ix_document_pages_document", "source_key", "external_id", "fingerprint"),
    )
