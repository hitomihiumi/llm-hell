import pytest

from app.services.agent.control import (
    STEP_DECISION_KEY,
    clear_cancel,
    is_cancelled,
    request_cancel,
    set_step_decision,
    wait_for_step_decision,
)


class FakeRedis:
    def __init__(self):
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


@pytest.mark.asyncio
async def test_request_cancel_and_is_cancelled() -> None:
    redis = FakeRedis()
    assert not await is_cancelled(redis, "run-1")
    await request_cancel(redis, "run-1")
    assert await is_cancelled(redis, "run-1")


@pytest.mark.asyncio
async def test_cancel_is_scoped_to_run_id() -> None:
    redis = FakeRedis()
    await request_cancel(redis, "run-1")
    assert not await is_cancelled(redis, "run-2")


@pytest.mark.asyncio
async def test_clear_cancel() -> None:
    redis = FakeRedis()
    await request_cancel(redis, "run-1")
    await clear_cancel(redis, "run-1")
    assert not await is_cancelled(redis, "run-1")


@pytest.mark.asyncio
async def test_wait_for_step_decision_returns_immediately_when_already_set() -> None:
    redis = FakeRedis()
    await set_step_decision(redis, "run-1", "approve")

    decision = await wait_for_step_decision(redis, "run-1", poll_interval=0.01, max_wait_seconds=1)
    assert decision == "approve"


@pytest.mark.asyncio
async def test_wait_for_step_decision_consumes_the_flag() -> None:
    redis = FakeRedis()
    await set_step_decision(redis, "run-1", "reject")
    await wait_for_step_decision(redis, "run-1", poll_interval=0.01, max_wait_seconds=1)

    assert redis._store.get(STEP_DECISION_KEY.format(run_id="run-1")) is None


@pytest.mark.asyncio
async def test_wait_for_step_decision_returns_cancelled_when_run_is_cancelled() -> None:
    redis = FakeRedis()
    await request_cancel(redis, "run-1")

    decision = await wait_for_step_decision(redis, "run-1", poll_interval=0.01, max_wait_seconds=1)
    assert decision == "cancelled"


@pytest.mark.asyncio
async def test_wait_for_step_decision_times_out() -> None:
    redis = FakeRedis()
    decision = await wait_for_step_decision(redis, "run-1", poll_interval=0.01, max_wait_seconds=0.03)
    assert decision is None


@pytest.mark.asyncio
async def test_wait_for_step_decision_polls_until_set(monkeypatch) -> None:
    redis = FakeRedis()
    calls = {"n": 0}

    real_get = redis.get

    async def flaky_get(key: str):
        calls["n"] += 1
        if calls["n"] < 3:
            return None
        return await real_get(key)

    await set_step_decision(redis, "run-1", "approve")
    redis.get = flaky_get

    decision = await wait_for_step_decision(redis, "run-1", poll_interval=0.001, max_wait_seconds=1)
    assert decision == "approve"
    assert calls["n"] >= 3
