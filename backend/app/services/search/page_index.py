"""Searching the pages we transcribed ourselves.

A source's own index is the source's own business, and Drive's covers a PDF's
text layer and nothing else. Everything printed inside a diagram is invisible
to it - which means the vision model could describe a pinout perfectly and the
document still would not come back when somebody asked for that pin.

This is the other half: the transcriptions are stored as they are produced and
searched here, so a term that exists only in a picture finds its document. The
index fills in as documents are read rather than being built up front, which
suits a demo and, more importantly, means nothing is ever transcribed twice.

Matching is `ILIKE` over the same reduced terms every source gets, not a
`tsvector`. The schema has to build on SQLite for the test suite, and for a
corpus of hundreds of pages the difference is not measurable. When it is, the
upgrade is an expression index in a migration - no column type changes, and
nothing in this module's contract moves.
"""

import logging

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_page import DocumentPage
from app.services.mcp.connector import search_terms

logger = logging.getLogger("llmhell.page_index")

# How many pages one query may match. A generous cap: these are grouped into
# documents afterwards, so the number of results a user sees is much smaller.
MAX_MATCHED_PAGES = 60


async def load_pages(
    db: AsyncSession, *, source_key: str, external_id: str, fingerprint: str
) -> dict[int, str]:
    """Page index -> transcription for one version of one document.

    Returns what was stored, including the empty strings that record "this
    page was read and had nothing on it". The caller distinguishes a page that
    is absent from a page that is known to be blank, which is the difference
    between rendering it again and not.
    """
    rows = (
        await db.execute(
            select(DocumentPage).where(
                DocumentPage.source_key == source_key,
                DocumentPage.external_id == external_id,
                DocumentPage.fingerprint == fingerprint,
            )
        )
    ).scalars()
    return {row.page_index: row.text for row in rows}


async def save_pages(
    db: AsyncSession,
    *,
    source_key: str,
    external_id: str,
    fingerprint: str,
    title: str,
    url: str | None,
    pages: dict[int, str],
) -> int:
    """Store transcriptions, replacing any reading of an older version.

    Old versions are deleted rather than kept. A stale transcription is worse
    than none: it would keep matching a search and citing a page whose content
    has since changed, and there is no way for a reader to tell.
    """
    if not pages:
        return 0

    await db.execute(
        delete(DocumentPage).where(
            DocumentPage.source_key == source_key,
            DocumentPage.external_id == external_id,
            DocumentPage.fingerprint != fingerprint,
        )
    )

    existing = await load_pages(
        db, source_key=source_key, external_id=external_id, fingerprint=fingerprint
    )
    written = 0
    for page_index, text in pages.items():
        if page_index in existing:
            continue
        db.add(
            DocumentPage(
                source_key=source_key,
                external_id=external_id,
                page_index=page_index,
                fingerprint=fingerprint,
                title=title,
                url=url,
                text=text,
            )
        )
        written += 1

    await db.flush()
    return written


async def search_pages(
    db: AsyncSession, query: str, *, source_key: str, limit: int
) -> list[tuple[str, str, str | None, int, str]]:
    """Documents whose transcribed pages match, best first.

    Returns `(external_id, title, url, page_index, text)` for the single best
    page of each document - one card per document, not one per page, because
    five pages of the same datasheet crowding out four other files is not a
    better result list.

    A query that reduces to no terms matches nothing rather than everything:
    "what is it" is not a request for the entire index.
    """
    terms = search_terms(query)
    if not terms:
        return []

    conditions = [DocumentPage.text.ilike(f"%{term}%") for term in terms]
    rows = list(
        (
            await db.execute(
                select(DocumentPage)
                .where(DocumentPage.source_key == source_key, or_(*conditions))
                .limit(MAX_MATCHED_PAGES)
            )
        )
        .scalars()
        .all()
    )

    def matched(text: str) -> int:
        lowered = text.lower()
        return sum(1 for term in terms if term.lower() in lowered)

    # Best page per document: the one matching the most distinct terms.
    best: dict[str, DocumentPage] = {}
    for row in rows:
        current = best.get(row.external_id)
        if current is None or matched(row.text) > matched(current.text):
            best[row.external_id] = row

    ordered = sorted(best.values(), key=lambda row: (-matched(row.text), row.external_id))
    return [
        (row.external_id, row.title, row.url, row.page_index, row.text)
        for row in ordered[:limit]
    ]
