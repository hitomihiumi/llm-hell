"""The semantic index, as a source like any other.

It could have been spliced into the pipeline ahead of the sources - run the
vector search, then the lexical ones, then merge on some rule written for the
occasion. Making it a connector instead means it goes through the same
reciprocal rank fusion as GitLab and Drive, appears in `source_status` with
its own hit count and errors, honours the per-source cap, and shows up in the
source filter. It earns its place in a result list rather than being given one.

There is no MCP server behind it. `kind` says so, and `health()` reports the
size of the index instead of a tool list - the question worth asking of this
source is not "is it reachable" but "has anything been indexed yet".
"""

import logging
import time
from typing import Any

from sqlalchemy import func, select

from app.core.config import Settings
from app.schemas.search import SearchHit
from app.services.mcp.connector import SearchContext, SourceResult, excerpt_around
from app.services.search import semantic

logger = logging.getLogger("llmhell.mcp.semantic")

SOURCE_SEMANTIC = "semantic"

SNIPPET_CHARS = 400


class SemanticConnector:
    kind = "semantic"

    def __init__(self, settings: Settings, *, key: str = SOURCE_SEMANTIC):
        self.key = key
        self._settings = settings

    async def health(self) -> dict[str, Any]:
        """How much is indexed, rather than whether a server answers.

        An empty index is the failure mode that matters here: the source is
        perfectly reachable and returns nothing, which from the outside looks
        exactly like a corpus with no answer in it.
        """
        return {
            "ok": True,
            "kind": self.kind,
            "model": self._settings.embeddings_model,
            "note": "run `manage.py index-corpus` to fill or refresh the index",
        }

    async def search(self, query: str, *, limit: int, ctx: SearchContext) -> SourceResult:
        started = time.monotonic()
        result = SourceResult(source_key=self.key)
        try:
            matches = await semantic.search(
                ctx.db,
                query=query,
                settings=self._settings,
                http_client=ctx.http_client,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001 - a source reports failure as data
            result.error = f"semantic search failed: {exc}"
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            return result

        total = await ctx.db.execute(
            select(func.count()).select_from(semantic.DocumentChunk).where(
                semantic.DocumentChunk.model == self._settings.embeddings_model
            )
        )
        indexed = total.scalar_one_or_none() or 0

        for rank, match in enumerate(matches):
            result.hits.append(
                SearchHit(
                    # The id and source of the document itself, not of this
                    # retriever. A semantically-found Drive file *is* a Drive
                    # file: presenting it as some other source would give the
                    # same document two entries in one result list, competing
                    # for the same slots, and would break the content route,
                    # which resolves an id by its source prefix.
                    #
                    # This is what makes the whole thing hybrid rather than
                    # merely additional - fusion sums a document's lexical and
                    # semantic contributions instead of ranking two copies of
                    # it against each other.
                    id=str(match["meta"].get("hit_id") or f"{match['source_key']}:{match['external_id']}"),
                    source=match["source_key"],
                    kind="document",
                    external_id=match["external_id"],
                    title=match["title"] or match["external_id"],
                    # A grid is not excerpted: `excerpt_around` keeps whichever
                    # rows happen to be adjacent, and the rows that give a cell
                    # its meaning are never adjacent - they are the header band
                    # the chunker deliberately repeated into every chunk, and a
                    # window over the middle would cut it straight off again.
                    snippet=(
                        match["text"]
                        if match.get("snippet_format") == "grid"
                        else excerpt_around(match["text"], query, SNIPPET_CHARS)
                    ),
                    snippet_format=match.get("snippet_format") or "text",
                    url=match["url"],
                    rank_in_source=rank,
                    # What makes this retriever safe for a document whose
                    # answer is a picture. A chunk is text, and a text chunk
                    # cannot say which side of the MCU the USB port is on -
                    # so the hit advertises its pages and the answer stage
                    # attaches them exactly as it does for a lexical hit.
                    preview_pages=match["meta"].get("preview_pages"),
                    matched_pages=match.get("matched_pages") or None,
                    # Kept for display and debugging, and deliberately not used
                    # for ordering: fusion consumes position, and a cosine on
                    # an unrelated scale is exactly what RRF exists to avoid.
                    source_score=round(match["score"], 4),
                    raw=match if ctx.debug else None,
                )
            )

        covered = await ctx.db.execute(
            select(semantic.DocumentChunk.source_key)
            .where(semantic.DocumentChunk.model == self._settings.embeddings_model)
            .distinct()
        )
        # Which sources this index can answer for. Reported because with the
        # lexical connectors switched off it is the only thing that knows: a
        # Drive document served from here is still a Drive document, and
        # anything reasoning about coverage needs to be told so rather than
        # inferring it from which connectors are enabled.
        result.detail = {
            "indexed_chunks": indexed,
            "model": self._settings.embeddings_model,
            "indexed_sources": sorted(covered.scalars().all()),
        }
        if not indexed:
            # Not an error - nothing is broken - but a search against an empty
            # index should say so rather than looking like a corpus with no
            # answer in it.
            result.degraded = True
            result.detail["warning"] = "nothing indexed yet"
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result
