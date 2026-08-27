"""The semantic index: chunking, storing, and searching by meaning.

This is the half of retrieval the stack never had. Everything else matches
spellings; this matches meaning, which is what lets "чи є тут гіроскоп" find a
datasheet that only ever says `IMU: MPU6000`.

Three things live here and nothing else does:

  * cutting a document into overlapping passages,
  * writing them with their vectors, skipping a document whose content has
    not changed since it was last indexed,
  * scoring a question against them.

It is deliberately not a connector. `SemanticConnector` in
`app/services/mcp/semantic.py` wraps `search()` so fusion, per-source status
and the source filter all treat it as one more source - which means it earns
its place against the others through the same RRF everything else goes
through, rather than being spliced in ahead of them.
"""

import logging
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.document_chunk import DocumentChunk
from app.services import sheets
from app.services.search.embeddings import EmbeddingError, cosine, embed

logger = logging.getLogger("llmhell.semantic")

# Characters, not tokens. A token count would need the tokenizer for whichever
# model is configured, and the difference does not change what a passage is:
# roughly a paragraph or two, small enough that its vector describes one idea
# rather than averaging several.
CHUNK_CHARS = 900
# Enough that a sentence spanning a boundary is whole on one side of it.
CHUNK_OVERLAP = 150


def chunk(text: str, *, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Cut a document into overlapping passages, preferring paragraph breaks.

    The break is moved back to the last blank line or newline inside the
    window when there is one. A passage cut mid-sentence embeds as something
    slightly other than what it says, and the cost of looking for a better
    boundary is one string search per chunk.
    """
    cleaned = (text or "").replace("\r\n", "\n").strip()
    if not cleaned:
        return []
    if len(cleaned) <= size:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + size, len(cleaned))
        if end < len(cleaned):
            window = cleaned[start:end]
            # Prefer a paragraph break, then any line break, then give up and
            # cut where the size says.
            for separator in ("\n\n", "\n"):
                cut = window.rfind(separator)
                # Honoured unless it is so early that the passage becomes a
                # scrap. A quarter, not a half: a short opening paragraph
                # followed by a wall of text is a real shape, and refusing to
                # break there cut the paragraph in half instead.
                if cut > size // 4:
                    end = start + cut
                    break
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(cleaned):
            break
        start = max(end - overlap, start + 1)
    return chunks


def chunk_pages(
    pages: list[str], *, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP
) -> list[tuple[int | None, str]]:
    """Cut a paginated document into passages that remember their page.

    Each page is chunked on its own rather than the pages being joined first,
    which costs a few more chunks on short pages and buys the thing this is
    for: a passage never spans a page boundary, so the page it names is the
    page it is actually on. A chunk stitched across pages 3 and 4 could only
    ever attach one of them, and would be wrong half the time.

    Empty pages are skipped rather than stored - a blank page has nothing to
    match - but they still consume their index, because the index *is* the
    page number and renumbering would attach the wrong picture.
    """
    out: list[tuple[int | None, str]] = []
    for index, text in enumerate(pages):
        for piece in chunk(text, size=size, overlap=overlap):
            out.append((index, piece))
    return out


# A rendered spreadsheet starts with this, from `sheets.render`. It is how a
# grid is recognised here without the caller having to say so.
GRID_MARKER = "sheet: "


def is_grid(text: str) -> bool:
    return (text or "").lstrip().startswith(GRID_MARKER)


def split_tabs(text: str) -> list[str]:
    """A workbook, split back into one section per tab.

    `sheets.render_tabs` concatenates every tab, each introduced by its own
    `sheet: 'Name'!range` line. Treating that as one grid is not a small
    inaccuracy - it takes the FIRST tab's header band and prefixes it to every
    other tab's rows, so a row from Spring Semester gets resolved against
    Fall Semester's months and days. Measured: asked about the second tab of
    the Gantt workbook, five chunks of Spring Semester data carried Fall
    Semester's calendar, and the answer was confidently about the wrong half
    of the year.
    """
    lines = (text or "").replace(chr(13) + chr(10), chr(10)).strip().split(chr(10))
    sections: list[list[str]] = []
    for line in lines:
        if line.startswith(GRID_MARKER) or not sections:
            sections.append([])
        sections[-1].append(line)
    return [chr(10).join(section) for section in sections if any(part.strip() for part in section)]


def chunk_grid(text: str, *, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Cut a rendered spreadsheet, repeating the header band in every piece.

    A grid chunked like prose is a grid destroyed. The rows that give a cell
    its meaning are at the very top of the sheet and are never adjacent to the
    data - so a chunk reading `R31: B=3D-друк деталей  G=X` says only that
    something happened in column G, and the row that says G is the 18th of
    September went into a different chunk. Measured on the Gantt sheet: the
    header band landed in chunk 0 and every data row in chunks 1 and 2, and
    the answer to "when did we finish the chassis design" became "the table
    does not give a date".

    So the band is repeated. It costs its length in every chunk - a few
    hundred characters - and it is the difference between a row that can be
    read and one that cannot.

    Each **tab** is cut on its own, with its own band. See `split_tabs`.
    """
    cleaned = (text or "").replace(chr(13) + chr(10), chr(10)).strip()
    if not cleaned:
        return []

    chunks: list[str] = []
    for section in split_tabs(cleaned):
        chunks.extend(_chunk_one_tab(section, size=size, overlap=overlap))
    return chunks


def _chunk_one_tab(text: str, *, size: int, overlap: int) -> list[str]:
    lines = text.split(chr(10))
    band = lines[: 1 + sheets.HEADER_ROWS]
    body = lines[1 + sheets.HEADER_ROWS :]
    if not body:
        return [text]

    prefix = chr(10).join(band)
    # Whatever is left for data after the band is repeated. Guarded so a
    # pathologically wide header cannot leave a chunk with no room at all.
    room = max(size - len(prefix) - 1, size // 4)

    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for line in body:
        if current and used + len(line) + 1 > room:
            chunks.append(prefix + chr(10) + chr(10).join(current))
            # One row of overlap, so a row split across the boundary of two
            # chunks is whole in at least one of them.
            current = current[-1:] if overlap else []
            used = sum(len(item) + 1 for item in current)
        current.append(line)
        used += len(line) + 1
    if current:
        chunks.append(prefix + chr(10) + chr(10).join(current))
    return chunks


def fingerprint(text: str) -> str:
    """Cheap identity for a document's content.

    Length, exactly as `document_pages` does it: not a hash, and it does not
    need to be. The failure it prevents is an edit that changes the length,
    which is nearly all of them, and it costs nothing to compute.
    """
    return str(len(text or ""))


async def is_indexed(
    db: AsyncSession, *, source_key: str, external_id: str, text: str, model: str
) -> bool:
    """Whether this exact version of this document is already in the index."""
    found = await db.execute(
        select(DocumentChunk.id)
        .where(
            DocumentChunk.source_key == source_key,
            DocumentChunk.external_id == external_id,
            DocumentChunk.fingerprint == fingerprint(text),
            DocumentChunk.model == model,
        )
        .limit(1)
    )
    return found.scalar_one_or_none() is not None


async def stored_text(
    db: AsyncSession, *, source_key: str, external_id: str, model: str
) -> str | None:
    """The text this document was indexed from, reassembled, or None.

    The crawler already downloaded and parsed this document; re-downloading it
    to build a snippet is paying twice for the same bytes. Measured: a Drive
    search took 11 seconds, essentially all of it enrichment fetching document
    text, while the semantic search over the same corpus took 0.66.

    Chunks overlap by design, so the joined text repeats a little at each
    boundary. That is fine for a snippet - it is excerpted around the query
    before anyone sees it - and fixing it would mean storing the original
    text a second time.

    None when the document is not indexed, which is the honest answer: the
    caller then fetches it as it always did.
    """
    rows = (
        await db.execute(
            select(DocumentChunk.text)
            .where(
                DocumentChunk.source_key == source_key,
                DocumentChunk.external_id == external_id,
                DocumentChunk.model == model,
            )
            .order_by(DocumentChunk.chunk_index)
        )
    ).scalars().all()
    if not rows:
        return None
    return chr(10).join(rows)


async def index_document(
    db: AsyncSession,
    *,
    source_key: str,
    external_id: str,
    title: str,
    url: str | None,
    text: str,
    settings: Settings,
    http_client: httpx.AsyncClient,
    meta: dict[str, Any] | None = None,
    pages: list[str] | None = None,
) -> int:
    """Store this document's passages and their vectors. Returns how many.

    `pages` is the document's text page by page, when it has pages. Given it,
    every chunk records which page it came from, and the answer stage can
    attach that page's picture instead of guessing which pages look
    interesting. Without it the document is chunked whole and the chunks carry
    no page, which is correct for everything that has none.

    Every previous version of the document is removed first, so an edit
    replaces rather than accumulates - otherwise a document edited five times
    would answer five different ways at once.
    """
    grid = is_grid(text)
    if pages:
        numbered: list[tuple[int | None, str]] = chunk_pages(pages)
    elif grid:
        numbered = [(None, piece) for piece in chunk_grid(text)]
    else:
        numbered = [(None, piece) for piece in chunk(text)]
    if not numbered:
        return 0

    pieces = [piece for _, piece in numbered]
    vectors = await embed(pieces, settings=settings, http_client=http_client, kind="passage")

    await db.execute(
        delete(DocumentChunk).where(
            DocumentChunk.source_key == source_key, DocumentChunk.external_id == external_id
        )
    )
    stamp = fingerprint(text)
    for position, ((page, piece), vector) in enumerate(zip(numbered, vectors, strict=True)):
        db.add(
            DocumentChunk(
                source_key=source_key,
                external_id=external_id,
                chunk_index=position,
                page_index=page,
                fingerprint=stamp,
                title=title,
                url=url,
                text=piece,
                embedding=vector,
                dims=len(vector),
                model=settings.embeddings_model,
                # Recorded by whoever actually cut the text, not by the
                # caller. The crawler learns a hit's `snippet_format` from
                # the *listing*, where a spreadsheet has not been rendered
                # yet and so reports plain text - and the flag then told the
                # answer prompt to treat a grid as prose, which excerpts a
                # window over the middle and cuts off the very header band
                # the grid chunker had just repeated into every chunk.
                meta={**(meta or {}), "snippet_format": "grid" if grid else "text"},
            )
        )
    await db.commit()
    return len(pieces)


async def search(
    db: AsyncSession,
    *,
    query: str,
    settings: Settings,
    http_client: httpx.AsyncClient,
    limit: int,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    """The best-matching documents for this question.

    Scored per *document*, not per chunk, taking each document's best passage.
    A long document cut into forty passages would otherwise take forty of the
    result slots on the strength of being long - the same defect that had
    GitLab's `pnpm-lock.yaml` filling half a result list, and the reason
    fusion scores per document too.
    """
    statement = select(DocumentChunk).where(DocumentChunk.model == settings.embeddings_model)
    if sources:
        statement = statement.where(DocumentChunk.source_key.in_(sources))
    rows = list((await db.execute(statement)).scalars().all())
    if not rows:
        return []

    try:
        vector = (await embed([query], settings=settings, http_client=http_client, kind="query"))[0]
    except EmbeddingError:
        # Re-raised to the connector, which reports it as a source failure -
        # an empty result here would be indistinguishable from an empty index.
        raise

    best: dict[tuple[str, str], dict[str, Any]] = {}
    # Every page of a document whose chunk cleared the floor, with the score
    # that got it there - so the answer can be given the pages that matched
    # rather than only the single best one. A diagram and its caption are
    # commonly two pages, and answering from one of them is answering from
    # half the picture.
    pages_by_document: dict[tuple[str, str], dict[int, float]] = {}
    for row in rows:
        if row.dims != len(vector):
            # A leftover from a different model. Skipped rather than compared,
            # because two models' vectors are not on the same scale and the
            # numbers would look perfectly reasonable.
            continue
        score = cosine(vector, row.embedding)
        key = (row.source_key, row.external_id)
        if row.page_index is not None and score >= settings.semantic_min_score:
            seen = pages_by_document.setdefault(key, {})
            if score > seen.get(row.page_index, -1.0):
                seen[row.page_index] = score
        current = best.get(key)
        if current is None or score > current["score"]:
            best[key] = {
                "source_key": row.source_key,
                "external_id": row.external_id,
                "page_index": row.page_index,
                "snippet_format": (row.meta or {}).get("snippet_format") or "text",
                "title": row.title,
                "url": row.url,
                "text": row.text,
                "score": score,
                "meta": row.meta,
            }

    for key, item in best.items():
        scored_pages = pages_by_document.get(key, {})
        item["matched_pages"] = [
            page for page, _ in sorted(scored_pages.items(), key=lambda pair: -pair[1])
        ]

    ranked = sorted(best.values(), key=lambda item: -item["score"])
    return [item for item in ranked if item["score"] >= settings.semantic_min_score][:limit]
