"""Keeping the semantic index up to date without anyone remembering to.

`manage.py index-corpus` fills the index, and it works - but it is a command
somebody has to run. An index nobody refreshes is worse than no index: it
answers confidently from a corpus that has moved on, and there is nothing in a
stale answer that says so. A document added this morning is simply invisible,
and the search reports no error while being wrong.

So the crawl runs on a timer inside the API process.

**Incremental, so the timer is cheap.** Every document is fingerprinted by
length and skipped when that version is already indexed, so a pass over an
unchanged corpus embeds nothing and costs one listing call per source. That is
what makes running it every half hour reasonable rather than reckless.

**One process only.** The compose file deliberately runs uvicorn without
`--workers`, because the Google MCP server can be a child of this process and
N workers would mean N subprocesses writing one OAuth token file. That is what
makes a plain in-process task safe here: there is exactly one of it. Should
this ever be scaled out, this loop needs a lock before it needs anything else,
or every replica will crawl the same corpus at the same time.

**Failures are logged and the loop continues.** A source that is down at
09:00 is usually up at 09:30, and a crawl that dies on the first refused
connection would leave the index frozen at whatever it held when the network
last hiccuped.
"""

import asyncio
import contextlib
import logging

import httpx
from sqlalchemy import select

from app.core.config import Settings
from app.core.db import SessionLocal
from app.models.endpoint import ModelEndpoint
from app.models.user import User
from app.services.mcp.registry import get_mcp_registry
from app.services.search import answer as answer_service
from app.services.search.crawl import crawl

logger = logging.getLogger("llmhell.indexer")

# How long to wait before the first pass. Long enough that a container coming
# up is not answering its first requests while also crawling every source, and
# short enough that a fresh deployment has an index within the hour.
FIRST_PASS_DELAY_SECONDS = 60.0


async def run_once(settings: Settings) -> None:
    """One full pass over the crawlable sources."""
    async with SessionLocal() as db:
        # Whoever exists. The crawl reads with the deployment's own
        # credentials - it is filling a shared index, not answering a
        # question for a particular person - but SearchContext wants a user
        # and the connectors read per-user tokens off it.
        user = (await db.execute(select(User).order_by(User.created_at))).scalars().first()
        if user is None:
            logger.info("semantic index: no users yet, nothing to crawl as")
            return

        endpoints = list((await db.execute(select(ModelEndpoint))).scalars().all())
        vision_endpoint = answer_service.select_vision_endpoint(endpoints, settings)

        async with httpx.AsyncClient() as http_client:
            report = await crawl(
                db,
                user=user,
                registry=get_mcp_registry(),
                settings=settings,
                http_client=http_client,
                vision_endpoint=vision_endpoint,
            )

    logger.info(
        "semantic index: %d indexed (%d chunks), %d unchanged, %d failed",
        report.indexed,
        report.chunks,
        report.skipped,
        report.failed,
    )
    for error in report.errors[:5]:
        logger.info("semantic index: %s", error)


async def _loop(settings: Settings) -> None:
    await asyncio.sleep(FIRST_PASS_DELAY_SECONDS)
    interval = max(1, settings.semantic_index_interval_minutes) * 60
    while True:
        try:
            await run_once(settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a bad pass must not end the loop
            logger.warning("semantic index pass failed: %s", exc)
        await asyncio.sleep(interval)


def start(settings: Settings) -> asyncio.Task | None:
    """Begin refreshing the index, or return None when that is switched off.

    Off by default in the sense that it does nothing unless the semantic
    source is enabled: a deployment not using the index should not be paying
    for a crawl of every source it has.
    """
    if not settings.semantic_enabled or settings.semantic_index_interval_minutes <= 0:
        logger.info("semantic index: automatic refresh is off")
        return None
    logger.info(
        "semantic index: refreshing every %d minutes",
        settings.semantic_index_interval_minutes,
    )
    return asyncio.create_task(_loop(settings), name="semantic-indexer")


async def stop(task: asyncio.Task | None) -> None:
    """Cancel the loop and wait for it, so shutdown does not race a crawl."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
