from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.models.project import Project, ProjectFile
from app.models.rating import RunOutcome
from app.models.run import Run
from app.models.user import User
from app.services.agent.loop import run_agent_loop
from app.services.projects.ingest import git_init_and_commit, write_files_to_workspace
from app.services.sandbox.base import ExecResult, SandboxBackend, SandboxConfig, SandboxHandle


class FakeRedis:
    def __init__(self):
        self._store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self._store[key] = value

    async def get(self, key):
        return self._store.get(key)

    async def delete(self, key):
        self._store.pop(key, None)

    async def publish(self, channel, message):
        pass


class FakeSandboxHandle(SandboxHandle):
    def __init__(self):
        self.stopped = False

    async def exec(self, command, *, timeout=None, max_output_lines=2000):
        return ExecResult(command="", exit_code=0, stdout="", stderr="", timed_out=False, truncated=False)

    async def stop(self) -> None:
        self.stopped = True

    @property
    def idle_seconds(self) -> float:
        return 0.0


class FakeSandboxBackend(SandboxBackend):
    def __init__(self):
        self.started: list[SandboxConfig] = []
        self.last_handle: FakeSandboxHandle | None = None

    async def start(self, config: SandboxConfig) -> SandboxHandle:
        self.started.append(config)
        self.last_handle = FakeSandboxHandle()
        return self.last_handle

    async def reap_idle(self, idle_timeout_seconds: float) -> int:
        return 0

    async def reap_orphans(self) -> int:
        return 0


def _test_settings(**overrides) -> Settings:
    base = dict(
        max_iterations_per_run=6,
        max_wall_time_seconds=60,
        sandbox_image="test-image",
        sandbox_cpus=1.0,
        sandbox_mem_limit="512m",
        sandbox_pids_limit=64,
        sandbox_command_timeout_seconds=10,
        projects_host_dir="/tmp/llmhell-test-projects",
        context_compaction_threshold=0.75,
    )
    base.update(overrides)
    return Settings(**base)


async def _seed_run(session, tmp_path: Path, *, mode: str = "autopilot") -> str:
    write_files_to_workspace(
        tmp_path, [("main.py", b"print(1)\n")], max_files=10, max_total_bytes=10_000, max_file_bytes=10_000
    )
    git_init_and_commit(tmp_path)

    session.add(User(id="user-1", username="tester", password_hash="x", role="user"))
    session.add(Project(id="proj-1", owner_id="user-1", name="demo", workspace_path=str(tmp_path)))
    session.add(ProjectFile(id="file-1", project_id="proj-1", path="main.py", size_bytes=12, token_count=5))

    for role, ep_id in (("planner", "ep-planner"), ("executor", "ep-executor")):
        session.add(
            ModelEndpoint(
                id=ep_id,
                name=role,
                base_url="http://mock-vllm/v1",
                model_id="glm-4.7",
                role=role,
                ctx_window=32768,
                tools_mode="native",
                reasoning_profile=DEFAULT_REASONING_PROFILE,
            )
        )

    run_id = "run-1"
    session.add(
        Run(
            id=run_id,
            project_id="proj-1",
            user_id="user-1",
            task_text="fix the failing test",
            mode=mode,
            status="queued",
            planner_endpoint_id="ep-planner",
            executor_endpoint_id="ep-executor",
            reasoning_level_planner="off",
            reasoning_level_executor="off",
        )
    )
    await session.flush()
    return run_id


@pytest.mark.asyncio
async def test_run_agent_loop_stops_at_max_iterations_when_executor_never_finishes(
    test_db_engine, mock_vllm_http_client, tmp_path: Path
) -> None:
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        run_id = await _seed_run(session, tmp_path)
        await session.commit()

        redis = FakeRedis()
        sandbox_backend = FakeSandboxBackend()

        status = await run_agent_loop(
            run_id,
            db=session,
            redis=redis,
            http_client=mock_vllm_http_client,
            sandbox_backend=sandbox_backend,
            settings=_test_settings(),
        )

        assert status == "failed"

        run = await session.get(Run, run_id)
        assert run.status == "failed"
        assert run.stop_reason == "max_iterations"
        assert run.plan is not None and len(run.plan) == 2
        assert run.finished_at is not None

        assert len(sandbox_backend.started) == 1
        assert sandbox_backend.started[0].workspace_host_path.endswith("proj-1/workspace")
        assert sandbox_backend.last_handle.stopped is True

        outcome = (
            await session.execute(
                RunOutcome.__table__.select().where(RunOutcome.run_id == run_id)
            )
        ).first()
        assert outcome is not None
        assert outcome.solved is False
        assert outcome.stop_reason == "max_iterations"


@pytest.mark.asyncio
async def test_run_agent_loop_approve_plan_mode_waits_and_then_cancels(
    test_db_engine, mock_vllm_http_client, tmp_path: Path
) -> None:
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        run_id = await _seed_run(session, tmp_path, mode="approve_plan")
        await session.commit()

        redis = FakeRedis()
        await redis.set(f"llmhell:run:{run_id}:cancel", "1")

        status = await run_agent_loop(
            run_id,
            db=session,
            redis=redis,
            http_client=mock_vllm_http_client,
            sandbox_backend=FakeSandboxBackend(),
            settings=_test_settings(max_wall_time_seconds=5),
        )

        assert status == "cancelled"
        run = await session.get(Run, run_id)
        assert run.status == "cancelled"
        assert run.stop_reason == "user_stop"
