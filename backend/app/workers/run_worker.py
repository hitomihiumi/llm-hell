"""arq worker entrypoint: runs the agent loop (`services.agent.loop`) as
a background job per run, and sweeps orphaned sandbox containers left
over from a previous crash/restart before accepting any.
"""

import logging

import httpx
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.services.agent.loop import run_agent_loop
from app.services.sandbox.docker_backend import DockerSandbox

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llmhell.worker")

settings = get_settings()


async def startup(ctx: dict) -> None:
    logger.info("llmhell worker started")
    try:
        sandbox = DockerSandbox()
        removed = await sandbox.reap_orphans()
        logger.info("Sandbox reaper: removed %d orphaned container(s)", removed)
    except Exception:  # noqa: BLE001 - no Docker daemon reachable (e.g. local dev) shouldn't crash the worker
        logger.warning("Could not reach Docker daemon; sandboxed runs will fail until it is", exc_info=True)
        sandbox = None
    ctx["sandbox"] = sandbox


async def shutdown(ctx: dict) -> None:
    logger.info("llmhell worker stopped")


async def execute_run(ctx: dict, run_id: str) -> str:
    if ctx.get("sandbox") is None:
        logger.error("Run %s cannot start: no Docker daemon available for the sandbox", run_id)
        raise RuntimeError("sandbox backend unavailable (no Docker daemon)")

    async with SessionLocal() as db:
        async with httpx.AsyncClient(timeout=120.0) as http_client:
            return await run_agent_loop(
                run_id,
                db=db,
                redis=ctx["redis"],
                http_client=http_client,
                sandbox_backend=ctx["sandbox"],
                settings=settings,
            )


class WorkerSettings:
    functions = [execute_run]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.max_concurrent_runs
