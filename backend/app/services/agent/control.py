"""Run control as Redis flags rather than callbacks threaded through the
loop: stopping a run and approving/rejecting a single stepwise tool call
are both fundamentally "wait for a *later*, separate HTTP request to flip
a flag" - polling a small Redis key is simpler and more robust across a
worker restart than holding an in-process future/event open for however
long a human takes to click a button.
"""

import asyncio
from typing import Literal

from redis.asyncio import Redis

CANCEL_KEY = "llmhell:run:{run_id}:cancel"
STEP_DECISION_KEY = "llmhell:run:{run_id}:step_decision"
PLAN_DECISION_KEY = "llmhell:run:{run_id}:plan_decision"

_FLAG_TTL_SECONDS = 3600

Decision = Literal["approve", "reject"]


async def request_cancel(redis: Redis, run_id: str) -> None:
    await redis.set(CANCEL_KEY.format(run_id=run_id), "1", ex=_FLAG_TTL_SECONDS)


async def is_cancelled(redis: Redis, run_id: str) -> bool:
    return (await redis.get(CANCEL_KEY.format(run_id=run_id))) is not None


async def clear_cancel(redis: Redis, run_id: str) -> None:
    await redis.delete(CANCEL_KEY.format(run_id=run_id))


async def set_decision(redis: Redis, key_template: str, run_id: str, decision: Decision) -> None:
    await redis.set(key_template.format(run_id=run_id), decision, ex=_FLAG_TTL_SECONDS)


async def _pop_decision(redis: Redis, key_template: str, run_id: str) -> str | None:
    key = key_template.format(run_id=run_id)
    value = await redis.get(key)
    if value is not None:
        await redis.delete(key)
        return value.decode() if isinstance(value, bytes) else value
    return None


async def wait_for_decision(
    redis: Redis,
    key_template: str,
    run_id: str,
    *,
    poll_interval: float = 1.0,
    max_wait_seconds: float = 1800,
) -> str | None:
    """Polls until a decision is set, the run is cancelled (returns
    "cancelled"), or `max_wait_seconds` elapses (returns None)."""
    elapsed = 0.0
    while elapsed < max_wait_seconds:
        if await is_cancelled(redis, run_id):
            return "cancelled"

        decision = await _pop_decision(redis, key_template, run_id)
        if decision is not None:
            return decision

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    return None


async def set_step_decision(redis: Redis, run_id: str, decision: Decision) -> None:
    await set_decision(redis, STEP_DECISION_KEY, run_id, decision)


async def wait_for_step_decision(
    redis: Redis, run_id: str, *, poll_interval: float = 1.0, max_wait_seconds: float = 1800
) -> str | None:
    return await wait_for_decision(
        redis, STEP_DECISION_KEY, run_id, poll_interval=poll_interval, max_wait_seconds=max_wait_seconds
    )


async def set_plan_decision(redis: Redis, run_id: str, decision: Decision) -> None:
    await set_decision(redis, PLAN_DECISION_KEY, run_id, decision)


async def wait_for_plan_decision(
    redis: Redis, run_id: str, *, poll_interval: float = 1.0, max_wait_seconds: float = 1800
) -> str | None:
    return await wait_for_decision(
        redis, PLAN_DECISION_KEY, run_id, poll_interval=poll_interval, max_wait_seconds=max_wait_seconds
    )
