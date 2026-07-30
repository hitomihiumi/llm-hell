import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.agent.events import Emitter, redis_channel, replay_events


class FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


@pytest.mark.asyncio
async def test_emit_writes_row_and_publishes_to_redis(test_db_engine) -> None:
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    redis = FakeRedis()

    async with session_maker() as session:
        emitter = Emitter("run-1", session, redis)
        row = await emitter.emit("token", {"text": "hello"})
        await session.commit()

        assert row.seq == 0
        assert row.type == "token"
        assert row.payload == {"text": "hello"}

        assert len(redis.published) == 1
        channel, message = redis.published[0]
        assert channel == redis_channel("run-1")
        decoded = json.loads(message)
        assert decoded["seq"] == 0
        assert decoded["type"] == "token"
        assert decoded["payload"] == {"text": "hello"}


@pytest.mark.asyncio
async def test_emit_increments_seq_across_calls(test_db_engine) -> None:
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    redis = FakeRedis()

    async with session_maker() as session:
        emitter = Emitter("run-1", session, redis)
        first = await emitter.emit("plan_ready", {})
        second = await emitter.emit("step_change", {})
        third = await emitter.emit("done", {})
        await session.commit()

    assert [first.seq, second.seq, third.seq] == [0, 1, 2]


@pytest.mark.asyncio
async def test_emitter_create_resumes_from_max_existing_seq(test_db_engine) -> None:
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    redis = FakeRedis()

    async with session_maker() as session:
        seeded = Emitter("run-1", session, redis)
        await seeded.emit("token", {})
        await seeded.emit("token", {})
        await session.commit()

    async with session_maker() as session:
        resumed = await Emitter.create("run-1", session, redis)
        row = await resumed.emit("token", {})
        await session.commit()
        assert row.seq == 2


@pytest.mark.asyncio
async def test_emitter_create_starts_at_zero_for_new_run(test_db_engine) -> None:
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    redis = FakeRedis()

    async with session_maker() as session:
        emitter = await Emitter.create("run-new", session, redis)
        row = await emitter.emit("token", {})
        await session.commit()
        assert row.seq == 0


@pytest.mark.asyncio
async def test_replay_events_returns_events_after_seq_in_order(test_db_engine) -> None:
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    redis = FakeRedis()

    async with session_maker() as session:
        emitter = Emitter("run-1", session, redis)
        for i in range(5):
            await emitter.emit("token", {"i": i})
        await session.commit()

    async with session_maker() as session:
        all_events = await replay_events(session, "run-1")
        assert [e.seq for e in all_events] == [0, 1, 2, 3, 4]

        since_2 = await replay_events(session, "run-1", since_seq=2)
        assert [e.seq for e in since_2] == [3, 4]


@pytest.mark.asyncio
async def test_replay_events_is_scoped_to_run_id(test_db_engine) -> None:
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    redis = FakeRedis()

    async with session_maker() as session:
        await Emitter("run-a", session, redis).emit("token", {})
        await Emitter("run-b", session, redis).emit("token", {})
        await session.commit()

    async with session_maker() as session:
        events_a = await replay_events(session, "run-a")
        assert len(events_a) == 1
