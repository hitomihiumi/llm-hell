import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.services.agent.control import is_cancelled


class FakeRedis:
    def __init__(self):
        self._store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self._store[key] = value

    async def get(self, key):
        return self._store.get(key)

    async def delete(self, key):
        self._store.pop(key, None)


@pytest.fixture(autouse=True)
def _no_real_enqueue(monkeypatch):
    async def fake_enqueue(run_id: str) -> None:
        return None

    monkeypatch.setattr("app.api.runs.enqueue_run", fake_enqueue)


@pytest.fixture
def fake_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr("app.api.runs.get_redis", lambda: redis)
    return redis


async def _create_project_and_endpoints(client, test_db_engine):
    project = (await client.post("/api/projects", json={"name": "demo"})).json()

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        planner_row = ModelEndpoint(
            name="planner",
            base_url="http://mock-vllm/v1",
            model_id="glm-4.7",
            role="planner",
            ctx_window=32768,
            tools_mode="json_protocol",
            reasoning_profile=DEFAULT_REASONING_PROFILE,
        )
        executor_row = ModelEndpoint(
            name="executor",
            base_url="http://mock-vllm/v1",
            model_id="glm-4.7-flash",
            role="executor",
            ctx_window=32768,
            tools_mode="json_protocol",
            reasoning_profile=DEFAULT_REASONING_PROFILE,
        )
        session.add_all([planner_row, executor_row])
        await session.commit()
        await session.refresh(planner_row)
        await session.refresh(executor_row)

    return project, {"id": planner_row.id}, {"id": executor_row.id}


@pytest.mark.asyncio
async def test_create_and_get_run(logged_in_client, test_db_engine) -> None:
    project, planner, executor = await _create_project_and_endpoints(logged_in_client, test_db_engine)

    create = await logged_in_client.post(
        "/api/runs",
        json={
            "project_id": project["id"],
            "task_text": "fix the bug",
            "mode": "autopilot",
            "planner_endpoint_id": planner["id"],
            "executor_endpoint_id": executor["id"],
        },
    )
    assert create.status_code == 200
    body = create.json()
    assert body["status"] == "queued"
    assert body["mode"] == "autopilot"

    get = await logged_in_client.get(f"/api/runs/{body['id']}")
    assert get.status_code == 200
    assert get.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_create_run_rejects_unknown_project(logged_in_client) -> None:
    response = await logged_in_client.post(
        "/api/runs",
        json={
            "project_id": "does-not-exist",
            "task_text": "x",
            "planner_endpoint_id": "a",
            "executor_endpoint_id": "b",
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_runs_for_project(logged_in_client, test_db_engine) -> None:
    project, planner, executor = await _create_project_and_endpoints(logged_in_client, test_db_engine)
    await logged_in_client.post(
        "/api/runs",
        json={
            "project_id": project["id"],
            "task_text": "task 1",
            "planner_endpoint_id": planner["id"],
            "executor_endpoint_id": executor["id"],
        },
    )
    await logged_in_client.post(
        "/api/runs",
        json={
            "project_id": project["id"],
            "task_text": "task 2",
            "planner_endpoint_id": planner["id"],
            "executor_endpoint_id": executor["id"],
        },
    )

    listing = await logged_in_client.get(f"/api/runs?project_id={project['id']}")
    assert listing.status_code == 200
    assert len(listing.json()) == 2


@pytest.mark.asyncio
async def test_stop_run_sets_cancel_flag(logged_in_client, fake_redis, test_db_engine) -> None:
    project, planner, executor = await _create_project_and_endpoints(logged_in_client, test_db_engine)
    run = (
        await logged_in_client.post(
            "/api/runs",
            json={
                "project_id": project["id"],
                "task_text": "x",
                "planner_endpoint_id": planner["id"],
                "executor_endpoint_id": executor["id"],
            },
        )
    ).json()

    stop = await logged_in_client.post(f"/api/runs/{run['id']}/stop")
    assert stop.status_code == 200
    assert await is_cancelled(fake_redis, run["id"])


@pytest.mark.asyncio
async def test_plan_decision_rejected_when_not_awaiting_approval(logged_in_client, fake_redis, test_db_engine) -> None:
    project, planner, executor = await _create_project_and_endpoints(logged_in_client, test_db_engine)
    run = (
        await logged_in_client.post(
            "/api/runs",
            json={
                "project_id": project["id"],
                "task_text": "x",
                "planner_endpoint_id": planner["id"],
                "executor_endpoint_id": executor["id"],
            },
        )
    ).json()

    response = await logged_in_client.post(f"/api/runs/{run['id']}/plan/decision", json={"decision": "approve"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_other_user_cannot_access_run(api_client, test_db_engine, fake_redis) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        session.add_all(
            [
                User(username="owner", password_hash=hash_password("password123"), role="user"),
                User(username="intruder", password_hash=hash_password("password123"), role="user"),
            ]
        )
        await session.commit()

    await api_client.post("/api/auth/login", json={"username": "owner", "password": "password123"})
    project, planner, executor = await _create_project_and_endpoints(api_client, test_db_engine)
    run = (
        await api_client.post(
            "/api/runs",
            json={
                "project_id": project["id"],
                "task_text": "x",
                "planner_endpoint_id": planner["id"],
                "executor_endpoint_id": executor["id"],
            },
        )
    ).json()

    await api_client.post("/api/auth/login", json={"username": "intruder", "password": "password123"})
    response = await api_client.get(f"/api/runs/{run['id']}")
    assert response.status_code == 403


class FakePubSub:
    async def subscribe(self, channel):
        pass

    async def get_message(self, ignore_subscribe_messages=True, timeout=0.0):
        return None

    async def unsubscribe(self, channel):
        pass

    async def close(self):
        pass


class FakeRedisWithPubSub:
    def pubsub(self):
        return FakePubSub()


@pytest.mark.asyncio
async def test_events_endpoint_replays_persisted_events(logged_in_client, test_db_engine, monkeypatch) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.services.agent.events import Emitter

    monkeypatch.setattr("app.api.runs.get_redis", lambda: FakeRedisWithPubSub())

    project, planner, executor = await _create_project_and_endpoints(logged_in_client, test_db_engine)
    run = (
        await logged_in_client.post(
            "/api/runs",
            json={
                "project_id": project["id"],
                "task_text": "x",
                "planner_endpoint_id": planner["id"],
                "executor_endpoint_id": executor["id"],
            },
        )
    ).json()

    class NullRedis:
        async def publish(self, channel, message):
            pass

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        emitter = Emitter(run["id"], session, NullRedis())
        await emitter.emit("plan_ready", {"steps": []})
        await emitter.emit("step_change", {"index": 0})
        # A terminal event lets the replay loop end the stream on its own -
        # the run is already finished, so there's nothing to wait for live.
        await emitter.emit("done", {"status": "completed"})
        await session.commit()

    lines: list[str] = []
    async with logged_in_client.stream("GET", f"/api/runs/{run['id']}/events") as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            lines.append(line)

    text = "\n".join(lines)
    assert "event: plan_ready" in text
    assert "event: step_change" in text
    assert "event: done" in text
