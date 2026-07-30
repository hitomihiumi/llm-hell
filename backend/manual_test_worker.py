"""Throwaway manual-test stand-in for the real arq worker: polls for
queued runs and drives them through the real agent loop using a fake
sandbox backend (no Docker available in this dev environment), so the
browser-facing SSE/chat UI can be verified against a genuinely running
agent loop. Not part of the shipped app - delete after use.
"""

import asyncio
import logging

import httpx
import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models.run import Run
from app.services.agent.loop import run_agent_loop
from app.services.sandbox.base import ExecResult, SandboxBackend, SandboxConfig, SandboxHandle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("manual_worker")


class FakeSandboxHandle(SandboxHandle):
    async def exec(self, command, *, timeout=None, max_output_lines=2000):
        return ExecResult(command="", exit_code=0, stdout="ok\n", stderr="", timed_out=False, truncated=False)

    async def stop(self) -> None:
        pass

    @property
    def idle_seconds(self) -> float:
        return 0.0


class FakeSandboxBackend(SandboxBackend):
    async def start(self, config: SandboxConfig) -> SandboxHandle:
        return FakeSandboxHandle()

    async def reap_idle(self, idle_timeout_seconds: float) -> int:
        return 0

    async def reap_orphans(self) -> int:
        return 0


async def main() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///manual_test2.db")
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    redis_client = redis.Redis(host="127.0.0.1", port=6399, decode_responses=True)
    settings = Settings(
        max_iterations_per_run=25,
        max_wall_time_seconds=300,
        sandbox_image="unused",
        sandbox_cpus=1,
        sandbox_mem_limit="512m",
        sandbox_pids_limit=64,
        sandbox_command_timeout_seconds=30,
        projects_host_dir="./manual_projects2",
    )

    seen: set[str] = set()
    logger.info("manual worker polling for queued runs...")
    while True:
        async with session_maker() as db:
            rows = (await db.execute(select(Run).where(Run.status == "queued"))).scalars().all()
            for run in rows:
                if run.id in seen:
                    continue
                seen.add(run.id)
                logger.info("picked up run %s", run.id)
                async with httpx.AsyncClient(timeout=60.0) as http_client:
                    try:
                        status = await run_agent_loop(
                            run.id,
                            db=db,
                            redis=redis_client,
                            http_client=http_client,
                            sandbox_backend=FakeSandboxBackend(),
                            settings=settings,
                        )
                        logger.info("run %s finished: %s", run.id, status)
                    except Exception:
                        logger.exception("run %s crashed", run.id)
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
