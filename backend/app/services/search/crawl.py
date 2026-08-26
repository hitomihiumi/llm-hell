"""Walking the corpus and filling the semantic index.

The index has to come from somewhere, and there are two honest options: fill
it lazily as documents happen to be read, or walk everything once. This is the
walk. It costs a burst of source API calls up front and then answers every
question from local vectors, which is the right trade for a corpus this size -
and it means a document nobody has searched for yet is still findable, which
lazy filling cannot offer.

**Incremental by content, not by time.** Every document is fingerprinted by
length and skipped when that version is already indexed, so a second run over
an unchanged corpus embeds nothing and costs one listing call per source. That
is what makes this safe to run on a schedule rather than only by hand.

**Gentle by default.** GitLab rate-limits itself - `429 Too Many Requests`
from the GitLab API, not from the MCP server - when a search fans out across
projects, and a crawl is a much bigger fan-out than a search. So documents are
fetched one at a time with a pause between them, and a source that starts
refusing is abandoned for this run rather than hammered until it bans the
deployment.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.source import SOURCE_GITLAB, SOURCE_GOOGLE_DRIVE, SOURCE_POSTGRES_KB
from app.models.user import User
from app.services.llm import vision
from app.services.mcp.connector import SearchContext
from app.services.mcp.gitlab import GitLabConnector
from app.services.mcp.google import GoogleWorkspaceConnector
from app.services.mcp.postgres import PostgresKbConnector
from app.services.mcp.registry import McpRegistry
from app.services.search import semantic

logger = logging.getLogger("llmhell.crawl")

# Between document fetches. Small, but the difference between a crawl a
# source tolerates and one it starts refusing part way through.
PAUSE_SECONDS = 0.4

# Consecutive failures from one source before it is given up on for this run.
# A source that has refused three documents in a row is rate-limiting or down,
# and continuing only deepens the hole.
GIVE_UP_AFTER = 3


# What a failure has to look like to count against the give-up budget.
#
# A source that is rate-limiting or unreachable should be abandoned for this
# run; a file that simply cannot be read should not count at all. Drive holds
# video, and `manage_docs` answers a request to read an .mp4 with
# `400 INVALID_ARGUMENT` - correctly, it is not a document. Counting those,
# three adjacent videos would abandon the entire Drive crawl.
_SOURCE_IS_FAILING = (
    "429",
    "too many requests",
    "timeout",
    "timed out",
    "connection",
    "unreachable",
    "econnrefused",
    "503",
    "502",
    "500",
)


def _looks_unreadable(error: str) -> bool:
    """Whether this failure is one document's problem rather than the source's."""
    lowered = error.lower()
    return not any(marker in lowered for marker in _SOURCE_IS_FAILING)


@dataclass
class CrawlReport:
    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    chunks: int = 0
    per_source: dict[str, dict[str, int]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def record(self, source: str, outcome: str, chunks: int = 0) -> None:
        counts = self.per_source.setdefault(source, {"indexed": 0, "skipped": 0, "failed": 0})
        counts[outcome] += 1
        setattr(self, outcome, getattr(self, outcome) + 1)
        self.chunks += chunks


def _as_documents(result: Any) -> list[dict[str, Any]]:
    """A source's listing, reduced to what the crawl needs.

    Both ids are carried. `external_id` is what the index is keyed on - it
    identifies the document across responses. `hit_id` is what `fetch_content`
    takes, because that method was written for the content route, where a hit
    id is what the caller has.
    """
    return [
        {
            "external_id": hit.external_id or hit.id,
            "hit_id": hit.id,
            "title": hit.title,
            "url": hit.url,
            # How many pages this document can render as pictures. Carried so
            # a semantically-retrieved document still reaches the answer with
            # its images attached: a chunk is text, and a board layout is not.
            "preview_pages": hit.preview_pages,
        }
        for hit in result.hits
        if hit.external_id or hit.id
    ]


async def crawl(
    db: AsyncSession,
    *,
    user: User,
    registry: McpRegistry,
    settings: Settings,
    http_client: httpx.AsyncClient,
    sources: list[str] | None = None,
    limit_per_source: int = 200,
    force: bool = False,
    vision_endpoint: Any = None,
) -> CrawlReport:
    """Index every document the named sources can list.

    `force` re-embeds documents whose fingerprint already matches, which is
    what a change of embedding model needs - the vectors are stale even though
    the text is not.
    """
    report = CrawlReport()
    ctx = SearchContext(
        db=db, user=user, http_client=http_client, vision_endpoint=vision_endpoint
    )

    wanted = sources or [SOURCE_GOOGLE_DRIVE, SOURCE_GITLAB, SOURCE_POSTGRES_KB]
    for source_key in wanted:
        connector = registry.get(source_key)
        if connector is None:
            report.errors.append(f"{source_key}: no connector registered")
            continue

        try:
            documents = await _list_documents(connector, ctx, limit_per_source)
        except Exception as exc:  # noqa: BLE001 - reported, never raised through
            report.errors.append(f"{source_key}: could not list documents: {exc}")
            continue

        logger.info("crawl: %s has %d documents to consider", source_key, len(documents))
        consecutive_failures = 0

        for document in documents:
            external_id = document["external_id"]
            try:
                text = await _fetch_text(connector, ctx, external_id, document)
            except Exception as exc:  # noqa: BLE001
                if _looks_unreadable(str(exc)):
                    # Not a failure of the source - a file it cannot express as
                    # text, which a video is. Recorded as skipped so a run of
                    # them cannot exhaust the give-up budget and abandon every
                    # document after them.
                    report.record(source_key, "skipped")
                    logger.info("nothing readable in %s: %s", external_id, exc)
                    continue
                consecutive_failures += 1
                report.record(source_key, "failed")
                report.errors.append(f"{source_key}:{external_id}: {exc}")
                if consecutive_failures >= GIVE_UP_AFTER:
                    report.errors.append(
                        f"{source_key}: giving up after {GIVE_UP_AFTER} failures in a row"
                    )
                    break
                await asyncio.sleep(PAUSE_SECONDS)
                continue

            consecutive_failures = 0
            described = False
            if not (text or "").strip():
                text = await _describe_visually(
                    connector, ctx, document, http_client=http_client, settings=settings
                )
                described = bool(text.strip())
            if not (text or "").strip():
                report.record(source_key, "skipped")
                continue

            # Per-page text when the source can give it, so every chunk records
            # its page and the answer stage can attach the page that matched
            # rather than the page that looks most interesting. A source with
            # no pages returns None and the document is chunked whole, which is
            # correct for a spreadsheet, a source file or a database row.
            pages: list[str] | None = None
            reader = getattr(connector, "page_texts", None)
            if reader is not None:
                try:
                    pages = await reader(document.get("hit_id") or external_id)
                except Exception as exc:  # noqa: BLE001 - pagination is a bonus
                    logger.info("no per-page text for %s: %s", external_id, exc)

            if not force and await semantic.is_indexed(
                db,
                source_key=source_key,
                external_id=external_id,
                text=text,
                model=settings.embeddings_model,
            ):
                report.record(source_key, "skipped")
                continue

            try:
                chunks = await semantic.index_document(
                    db,
                    source_key=source_key,
                    external_id=external_id,
                    title=document.get("title") or external_id,
                    url=document.get("url"),
                    text=text,
                    settings=settings,
                    http_client=http_client,
                    # The id the source itself would mint for this document.
                    # Carried so a semantic hit is indistinguishable from the
                    # lexical one - same id, same source - which is what lets
                    # fusion merge them instead of ranking them against each
                    # other.
                    meta={
                        "hit_id": document.get("hit_id") or external_id,
                        "preview_pages": document.get("preview_pages"),
                        # True when the indexed text is a description of a
                        # picture rather than the document's own words. Kept
                        # so it is never mistaken for a quotable source.
                        "described": described,
                    },
                    pages=pages,
                )
            except Exception as exc:  # noqa: BLE001
                report.record(source_key, "failed")
                report.errors.append(f"{source_key}:{external_id}: embedding failed: {exc}")
                continue

            report.record(source_key, "indexed", chunks)
            await asyncio.sleep(PAUSE_SECONDS)

    return report


async def _describe_visually(
    connector: Any,
    ctx: SearchContext,
    document: dict[str, Any],
    *,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> str:
    """Words for a file that has none, so it can be found at all.

    An image file has no text layer, so the crawler skipped it and it never
    entered the index: asked the dimensions of a T-Motor U7, the search could
    not return the PNG that has them, because nothing about that PNG was
    searchable. Thirteen Drive documents were indexed and the drawing was not
    one of them.

    **This description is for finding the file, never for answering from it.**
    That distinction is the whole architecture: `page_images` still attaches
    the real picture to the answer prompt, and the model reads the drawing
    rather than somebody's paraphrase of it. A transcription is an index
    entry; the pixels are the evidence.

    Returns "" when there is no vision endpoint or nothing renders - the file
    is skipped exactly as before, which is a document missing from the index
    rather than a failed crawl.
    """
    if ctx.vision_endpoint is None:
        return ""
    renderer = getattr(connector, "page_images", None)
    if renderer is None:
        return ""

    try:
        rendered = await renderer(
            document.get("hit_id") or document["external_id"],
            max_pages=settings.vision_max_pages,
        )
    except Exception as exc:  # noqa: BLE001 - an undescribed file, not a failure
        logger.info("could not render %s for description: %s", document["external_id"], exc)
        return ""
    if not rendered:
        return ""

    described = await vision.describe_pages(
        ctx.vision_endpoint,
        dict(enumerate(rendered)),
        http_client=http_client,
        concurrency=settings.vision_concurrency,
    )
    parts = [text for _, text in sorted(described.items()) if text]
    if not parts:
        return ""
    # The title leads, because it is often the most searchable thing about a
    # drawing - a part number in a filename beats any description of it.
    return (document.get("title") or "") + chr(10) + chr(10).join(parts)


async def _list_documents(connector: Any, ctx: SearchContext, limit: int) -> list[dict[str, Any]]:
    """Everything a source will list, through its own search.

    An empty query rather than a new API path: the connectors turn that into
    each source's own "match everything" - `trashed = false` for Drive - so
    the crawl sees exactly what a search would, including whatever scoping the
    connector applies. Any source that cannot answer it lists nothing, which
    the report says out loud rather than silently indexing an empty corpus.
    """
    if not isinstance(connector, GitLabConnector | GoogleWorkspaceConnector | PostgresKbConnector):
        raise TypeError(f"no crawl strategy for {type(connector).__name__}")
    return _as_documents(await connector.search("", limit=limit, ctx=ctx))


async def _fetch_text(
    connector: Any, ctx: SearchContext, external_id: str, document: dict[str, Any]
) -> str:
    """The document's full text.

    Every connector exposes `fetch_content(hit_id, ctx=...)`, returning the
    same `{text: ...}` shape the content route serves - so the crawl reads a
    document exactly as opening it in the UI would, rather than through a
    second path that could drift from it.
    """
    fetched = await connector.fetch_content(document.get("hit_id") or external_id, ctx=ctx)
    if not fetched:
        return ""
    if isinstance(fetched, str):
        return fetched
    if isinstance(fetched, dict):
        return str(fetched.get("text") or fetched.get("content") or "")
    return str(getattr(fetched, "text", "") or "")
