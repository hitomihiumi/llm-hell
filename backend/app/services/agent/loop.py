"""Top-level orchestrator: generate a plan, gate it by the run's mode,
then work through steps with the executor - escalating back to the
planner after repeated failures on the same step - until the plan is
done, a limit is hit, or the run is stopped. Everything else in
`services.agent`/`services.context`/`services.sandbox` is a building
block this function wires together; this is the one place that actually
drives a run from queued to a terminal status.
"""

import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.endpoint import ModelEndpoint
from app.models.project import Project, ProjectFile
from app.models.rating import RunOutcome
from app.models.run import Run, UserIntervention
from app.services.agent.control import is_cancelled, wait_for_plan_decision
from app.services.agent.events import Emitter
from app.services.agent.executor import run_executor_step
from app.services.agent.planner import PlanParseError, PlanStep, generate_plan
from app.services.context.assembler import PinnedFile
from app.services.projects.diff import commit_changes
from app.services.projects.tree import FileEntry
from app.services.sandbox.base import SandboxBackend, SandboxConfig, workspace_host_path

logger = logging.getLogger("llmhell.agent.loop")

LoopStatus = Literal["completed", "failed", "cancelled"]

MAX_CONSECUTIVE_STEP_FAILURES = 2


async def _load_files(
    db: AsyncSession, project_id: str, workspace_path: Path
) -> tuple[list[FileEntry], list[PinnedFile]]:
    rows = (
        await db.execute(select(ProjectFile).where(ProjectFile.project_id == project_id))
    ).scalars().all()

    all_files = [
        FileEntry(id=r.id, path=r.path, size_bytes=r.size_bytes, token_count=r.token_count or 0, pinned=r.pinned)
        for r in rows
    ]
    pinned_files = []
    for entry in all_files:
        if not entry.pinned:
            continue
        target = workspace_path / entry.path
        if target.is_file():
            pinned_files.append(PinnedFile(entry=entry, content=target.read_text(encoding="utf-8", errors="replace")))

    return all_files, pinned_files


async def _finish_run(
    db: AsyncSession,
    emitter: Emitter,
    run: Run,
    *,
    status: LoopStatus,
    stop_reason: str,
    workspace_path: Path,
    total_iterations: int,
) -> None:
    commit_changes(workspace_path, f"LLM-Hell run {run.id} ({status})")

    run.status = status
    run.stop_reason = stop_reason
    run.finished_at = datetime.now(timezone.utc)

    intervention_count = (
        await db.execute(
            select(func.count()).select_from(UserIntervention).where(UserIntervention.run_id == run.id)
        )
    ).scalar_one()

    db.add(
        RunOutcome(
            run_id=run.id,
            solved=status == "completed",
            tests_passed=None,
            iterations_to_success=total_iterations if status == "completed" else None,
            user_interventions_count=intervention_count,
            stop_reason=stop_reason,
        )
    )
    await db.flush()
    await emitter.emit("done", {"status": run.status, "stop_reason": stop_reason})
    await db.commit()


async def run_agent_loop(
    run_id: str,
    *,
    db: AsyncSession,
    redis: Redis,
    http_client: httpx.AsyncClient,
    sandbox_backend: SandboxBackend,
    settings: Settings,
) -> LoopStatus:
    run = await db.get(Run, run_id)
    if run is None:
        raise ValueError(f"no such run: {run_id}")

    planner_endpoint = await db.get(ModelEndpoint, run.planner_endpoint_id)
    executor_endpoint = await db.get(ModelEndpoint, run.executor_endpoint_id)
    project = await db.get(Project, run.project_id)
    if planner_endpoint is None or executor_endpoint is None or project is None:
        raise ValueError(f"run {run_id} references a missing project or endpoint")

    workspace_path = Path(project.workspace_path)
    emitter = await Emitter.create(run_id, db, redis)

    run.status = "planning"
    run.started_at = datetime.now(timezone.utc)
    await db.commit()

    all_files, pinned_files = await _load_files(db, run.project_id, workspace_path)

    try:
        generated = await generate_plan(
            task_text=run.task_text,
            all_files=all_files,
            endpoint=planner_endpoint,
            reasoning_level=run.reasoning_level_planner,
            http_client=http_client,
        )
    except PlanParseError as exc:
        await emitter.emit("error", {"message": f"planner did not return a usable plan: {exc}"})
        await _finish_run(
            db, emitter, run, status="failed", stop_reason="plan_parse_error",
            workspace_path=workspace_path, total_iterations=0,
        )
        return "failed"

    run.plan = [asdict(s) for s in generated.steps]
    await emitter.emit("plan_ready", {"steps": run.plan, "reasoning": generated.reasoning})

    if run.mode == "approve_plan":
        run.status = "awaiting_plan_approval"
        await db.commit()

        decision = await wait_for_plan_decision(redis, run_id, max_wait_seconds=settings.max_wall_time_seconds)
        await db.refresh(run)  # picks up an edited plan, if the approval endpoint changed one

        if decision in ("cancelled", None, "reject"):
            reason = "user_stop" if decision == "cancelled" else ("timeout" if decision is None else "plan_rejected")
            await _finish_run(
                db, emitter, run, status="cancelled", stop_reason=reason,
                workspace_path=workspace_path, total_iterations=0,
            )
            return "cancelled"

    run.status = "running"
    await db.commit()

    sandbox_config = SandboxConfig(
        image=settings.sandbox_image,
        workspace_host_path=workspace_host_path(run.project_id, settings.projects_host_dir),
        cpus=settings.sandbox_cpus,
        mem_limit=settings.sandbox_mem_limit,
        pids_limit=settings.sandbox_pids_limit,
        command_timeout_seconds=settings.sandbox_command_timeout_seconds,
        labels={"llmhell.run_id": run_id},
    )
    sandbox = await sandbox_backend.start(sandbox_config)

    history: list[dict] = []
    retrieved_files: dict[str, str] = {}
    summary: str | None = None
    total_iterations = 0
    consecutive_failures = 0
    started_at_monotonic = time.monotonic()
    stop_reason: str | None = None
    final_status: LoopStatus = "completed"

    try:
        step_index = 0
        while step_index < len(run.plan):
            if await is_cancelled(redis, run_id):
                stop_reason, final_status = "user_stop", "cancelled"
                break
            if total_iterations >= settings.max_iterations_per_run:
                stop_reason, final_status = "max_iterations", "failed"
                break
            if time.monotonic() - started_at_monotonic >= settings.max_wall_time_seconds:
                stop_reason, final_status = "max_wall_time", "failed"
                break

            step = PlanStep(**run.plan[step_index])
            run.current_step_index = step_index
            await emitter.emit("step_change", {"index": step_index, "step": run.plan[step_index]})
            await db.commit()

            outcome = await run_executor_step(
                step=step,
                run=run,
                db=db,
                redis=redis,
                emitter=emitter,
                all_files=all_files,
                pinned_files=pinned_files,
                retrieved_files=retrieved_files,
                summary=summary,
                history=history,
                endpoint=executor_endpoint,
                reasoning_level=run.reasoning_level_executor,
                workspace_path=workspace_path,
                sandbox=sandbox,
                command_timeout_seconds=settings.sandbox_command_timeout_seconds,
                max_iterations=max(1, settings.max_iterations_per_run - total_iterations),
                http_client=http_client,
            )
            total_iterations += outcome.iterations_used

            if outcome.status == "cancelled":
                stop_reason, final_status = "user_stop", "cancelled"
                break

            if outcome.status == "done":
                consecutive_failures = 0
                step_index += 1
                continue

            consecutive_failures += 1
            if consecutive_failures < MAX_CONSECUTIVE_STEP_FAILURES:
                continue  # retry the same step

            try:
                revised = await generate_plan(
                    task_text=(
                        f"{run.task_text}\n\nThe previous plan got stuck at step "
                        f"'{step.title}': {outcome.summary}. Revise the remaining steps."
                    ),
                    all_files=all_files,
                    endpoint=planner_endpoint,
                    reasoning_level=run.reasoning_level_planner,
                    http_client=http_client,
                )
            except PlanParseError as exc:
                await emitter.emit("error", {"message": f"escalation failed: {exc}"})
                stop_reason, final_status = "escalation_failed", "failed"
                break

            run.plan = run.plan[:step_index] + [asdict(s) for s in revised.steps]
            await db.commit()
            await emitter.emit(
                "plan_ready", {"steps": run.plan, "reasoning": revised.reasoning, "escalated": True}
            )
            consecutive_failures = 0

        else:
            stop_reason, final_status = "plan_complete", "completed"

    except Exception as exc:  # noqa: BLE001 - an unexpected error must still leave the run in a terminal state
        logger.exception("Agent loop crashed for run %s", run_id)
        await emitter.emit("error", {"message": str(exc)})
        stop_reason, final_status = "internal_error", "failed"
    finally:
        await sandbox.stop()

    await _finish_run(
        db, emitter, run, status=final_status, stop_reason=stop_reason,
        workspace_path=workspace_path, total_iterations=total_iterations,
    )
    return final_status
