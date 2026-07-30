"""Shared Redis client for pub/sub (SSE fanout) and run-control flags -
distinct from `core.queue`'s arq connection pool, which is only for
enqueueing jobs.
"""

import redis.asyncio as redis

from app.core.config import get_settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client
