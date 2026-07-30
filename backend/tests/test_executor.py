from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.models.run import Run
from app.services.agent.executor import run_executor_step
from app.services.agent.planner import PlanStep
from app.services.agent.tools import FINISH_STEP, READ_FILE, RUN_COMMAND, WRITE_FILE
from app.services.sandbox.base import ExecResult, SandboxHandle


class FakeRedis:
    def __init__(self):
        self._store: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def set(self, key, value, ex=None):
        self._store[key] = value

    async def get(self, key):
        return self._store.get(key)

    async def delete(self, key):
        self._store.pop(key, None)

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


class FakeSandboxHandle(SandboxHandle):
    def __init__(self):
        self.calls = []

    async def exec(self, command, *, timeout=None, max_output_lines=2000):
        self.calls.append(command)
        return ExecResult(command="", exit_code=0, stdout="ok\n", stderr="", timed_out=False, truncated=False)

    async def stop(self) -> None:
        pass

    @property
    def idle_seconds(self) -> float:
        return 0.0


def _endpoint(tools_mode: str) -> ModelEndpoint:
    return ModelEndpoint(
        id="ep-1",
        name="test",
        base_url="http://mock-vllm/v1",
        model_id="glm-4.7",
        role="executor",
        ctx_window=32768,
        tools_mode=tools_mode,
        reasoning_profile=DEFAULT_REASONING_PROFILE,
    )


def _step() -> PlanStep:
    return PlanStep(id="step-1", title="Fix the bug", intent="because it's broken", files=["main.py"], done_when="tests pass")


async def _make_emitter(test_db_engine, run_id: str, redis):
    from app.services.agent.events import Emitter

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    session = session_maker()
    run = Run(
        id=run_id,
        project_id="proj-1",
        user_id="user-1",
        task_text="fix it",
        mode="autopilot",
        status="running",
        planner_endpoint_id="ep-planner",
        executor_endpoint_id="ep-executor",
    )
    session.add(run)
    await session.flush()
    emitter = await Emitter.create(run_id, session, redis)
    return session, run, emitter


@pytest.mark.asyncio
async def test_run_executor_step_reaches_done_via_native_finish_step(
    test_db_engine, mock_vllm_http_client, tmp_path: Path
) -> None:
    redis = FakeRedis()
    session, run, emitter = await _make_emitter(test_db_engine, "run-1", redis)

    outcome = await run_executor_step(
        step=_step(),
        run=run,
        db=session,
        redis=redis,
        emitter=emitter,
        all_files=[],
        pinned_files=[],
        retrieved_files={},
        summary=None,
        history=[],
        endpoint=_endpoint("native"),
        reasoning_level="off",
        workspace_path=tmp_path,
        sandbox=FakeSandboxHandle(),
        command_timeout_seconds=30,
        max_iterations=5,
        http_client=mock_vllm_http_client,
        tools=[FINISH_STEP],
    )

    assert outcome.status == "done"
    assert outcome.iterations_used == 1


@pytest.mark.asyncio
async def test_run_executor_step_reaches_done_via_json_protocol(
    test_db_engine, mock_vllm_http_client, tmp_path: Path
) -> None:
    redis = FakeRedis()
    session, run, emitter = await _make_emitter(test_db_engine, "run-2", redis)

    outcome = await run_executor_step(
        step=_step(),
        run=run,
        db=session,
        redis=redis,
        emitter=emitter,
        all_files=[],
        pinned_files=[],
        retrieved_files={},
        summary=None,
        history=[],
        endpoint=_endpoint("json_protocol"),
        reasoning_level="off",
        workspace_path=tmp_path,
        sandbox=FakeSandboxHandle(),
        command_timeout_seconds=30,
        max_iterations=5,
        http_client=mock_vllm_http_client,
        tools=[FINISH_STEP],
    )

    assert outcome.status == "done"
    assert outcome.summary == "Mock finished the step."


@pytest.mark.asyncio
async def test_run_executor_step_exhausts_when_never_finishes(
    test_db_engine, mock_vllm_http_client, tmp_path: Path
) -> None:
    redis = FakeRedis()
    session, run, emitter = await _make_emitter(test_db_engine, "run-3", redis)

    outcome = await run_executor_step(
        step=_step(),
        run=run,
        db=session,
        redis=redis,
        emitter=emitter,
        all_files=[],
        pinned_files=[],
        retrieved_files={},
        summary=None,
        history=[],
        endpoint=_endpoint("native"),
        reasoning_level="off",
        workspace_path=tmp_path,
        sandbox=FakeSandboxHandle(),
        command_timeout_seconds=30,
        max_iterations=3,
        http_client=mock_vllm_http_client,
        tools=[READ_FILE],
    )

    assert outcome.status == "exhausted"
    assert outcome.iterations_used == 3


@pytest.mark.asyncio
async def test_run_executor_step_stops_when_cancelled_before_first_iteration(
    test_db_engine, mock_vllm_http_client, tmp_path: Path
) -> None:
    from app.services.agent.control import request_cancel

    redis = FakeRedis()
    session, run, emitter = await _make_emitter(test_db_engine, "run-4", redis)
    await request_cancel(redis, "run-4")

    outcome = await run_executor_step(
        step=_step(),
        run=run,
        db=session,
        redis=redis,
        emitter=emitter,
        all_files=[],
        pinned_files=[],
        retrieved_files={},
        summary=None,
        history=[],
        endpoint=_endpoint("native"),
        reasoning_level="off",
        workspace_path=tmp_path,
        sandbox=FakeSandboxHandle(),
        command_timeout_seconds=30,
        max_iterations=5,
        http_client=mock_vllm_http_client,
        tools=[FINISH_STEP],
    )

    assert outcome.status == "cancelled"


@pytest.mark.asyncio
async def test_run_executor_step_runs_side_effect_tool_after_approval(
    test_db_engine, mock_vllm_http_client, tmp_path: Path
) -> None:
    from app.services.agent.control import set_step_decision

    redis = FakeRedis()
    session, run, emitter = await _make_emitter(test_db_engine, "run-5", redis)
    run.mode = "stepwise"
    await set_step_decision(redis, "run-5", "approve")

    sandbox = FakeSandboxHandle()
    outcome = await run_executor_step(
        step=_step(),
        run=run,
        db=session,
        redis=redis,
        emitter=emitter,
        all_files=[],
        pinned_files=[],
        retrieved_files={},
        summary=None,
        history=[],
        endpoint=_endpoint("native"),
        reasoning_level="off",
        workspace_path=tmp_path,
        sandbox=sandbox,
        command_timeout_seconds=30,
        max_iterations=1,
        http_client=mock_vllm_http_client,
        tools=[RUN_COMMAND],
        step_approval_timeout_seconds=2,
    )

    assert len(sandbox.calls) == 1
    assert "echo mock" in sandbox.calls[0][-1]
    assert outcome.status == "exhausted"  # RUN_COMMAND isn't a finish signal, so it exhausts after 1 iteration


@pytest.mark.asyncio
async def test_run_executor_step_rejected_side_effect_tool_is_not_executed(
    test_db_engine, mock_vllm_http_client, tmp_path: Path
) -> None:
    from app.services.agent.control import set_step_decision

    redis = FakeRedis()
    session, run, emitter = await _make_emitter(test_db_engine, "run-6", redis)
    run.mode = "stepwise"
    await set_step_decision(redis, "run-6", "reject")

    sandbox = FakeSandboxHandle()
    await run_executor_step(
        step=_step(),
        run=run,
        db=session,
        redis=redis,
        emitter=emitter,
        all_files=[],
        pinned_files=[],
        retrieved_files={},
        summary=None,
        history=[],
        endpoint=_endpoint("native"),
        reasoning_level="off",
        workspace_path=tmp_path,
        sandbox=sandbox,
        command_timeout_seconds=30,
        max_iterations=1,
        http_client=mock_vllm_http_client,
        tools=[RUN_COMMAND],
        step_approval_timeout_seconds=2,
    )

    assert len(sandbox.calls) == 0


@pytest.mark.asyncio
async def test_run_executor_step_updates_retrieved_files_on_write(
    test_db_engine, mock_vllm_http_client, tmp_path: Path
) -> None:
    retrieved_files: dict[str, str] = {}
    redis = FakeRedis()
    session, run, emitter = await _make_emitter(test_db_engine, "run-7", redis)

    await run_executor_step(
        step=_step(),
        run=run,
        db=session,
        redis=redis,
        emitter=emitter,
        all_files=[],
        pinned_files=[],
        retrieved_files=retrieved_files,
        summary=None,
        history=[],
        endpoint=_endpoint("native"),
        reasoning_level="off",
        workspace_path=tmp_path,
        sandbox=FakeSandboxHandle(),
        command_timeout_seconds=30,
        max_iterations=1,
        http_client=mock_vllm_http_client,
        tools=[WRITE_FILE],
    )

    assert retrieved_files == {"mock_output.txt": "mock content"}
    assert (tmp_path / "mock_output.txt").read_text() == "mock content"
