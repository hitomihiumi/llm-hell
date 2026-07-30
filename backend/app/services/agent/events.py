"""The SSE event stream a run publishes: written to `run_events` for
durable replay and fanned out over Redis pub/sub so multiple open tabs
(and a client that reconnects mid-run) see the same live stream. The
event *types* mirror the plan's list - `token`, `reasoning`,
`tool_call_start`/`tool_call_end`, `step_change`, `plan_ready`,
`context_update`, `compaction`, `usage`, `error`, `done`.
"""

import json
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import RunEvent

EVENT_TYPES = (
    "token",
    "reasoning",
    "tool_call_start",
    "tool_call_end",
    "step_change",
    "plan_ready",
    "context_update",
    "compaction",
    "usage",
    "error",
    "done",
)


def redis_channel(run_id: str) -> str:
    return f"llmhell:run:{run_id}:events"


def encode_event(seq: int, event_type: str, payload: dict[str, Any], created_at: datetime) -> str:
    return json.dumps(
        {"seq": seq, "type": event_type, "payload": payload, "created_at": created_at.isoformat()}
    )


class Emitter:
    """One instance per running loop - `_next_seq` is only safe as
    in-memory state because a single loop is the sole writer for its run's
    events."""

    def __init__(self, run_id: str, db: AsyncSession, redis: Redis, start_seq: int = 0):
        self.run_id = run_id
        self.db = db
        self.redis = redis
        self._next_seq = start_seq

    @classmethod
    async def create(cls, run_id: str, db: AsyncSession, redis: Redis) -> "Emitter":
        max_seq = (
            await db.execute(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id))
        ).scalar_one()
        return cls(run_id, db, redis, start_seq=0 if max_seq is None else max_seq + 1)

    async def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> RunEvent:
        payload = payload or {}
        seq = self._next_seq
        self._next_seq += 1
        created_at = datetime.now(timezone.utc)

        row = RunEvent(run_id=self.run_id, seq=seq, type=event_type, payload=payload, created_at=created_at)
        self.db.add(row)
        await self.db.flush()

        await self.redis.publish(redis_channel(self.run_id), encode_event(seq, event_type, payload, created_at))
        return row


async def replay_events(db: AsyncSession, run_id: str, since_seq: int = -1) -> list[RunEvent]:
    result = await db.execute(
        select(RunEvent)
        .where(RunEvent.run_id == run_id, RunEvent.seq > since_seq)
        .order_by(RunEvent.seq)
    )
    return list(result.scalars().all())
