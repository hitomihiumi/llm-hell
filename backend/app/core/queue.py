"""The API process's side of the arq queue: a lazily-created connection
pool for enqueueing jobs onto the same Redis the `worker` container's arq
process consumes from (see app/workers/run_worker.py).
"""

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from app.core.config import get_settings

_pool: ArqRedis | None = None


async def get_queue() -> ArqRedis:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def enqueue_run(run_id: str) -> None:
    queue = await get_queue()
    await queue.enqueue_job("execute_run", run_id)
