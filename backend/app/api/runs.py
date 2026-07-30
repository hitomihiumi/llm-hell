import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import CurrentUser
from app.core.queue import enqueue_run
from app.core.redis_client import get_redis
from app.models.project import Project
from app.models.run import Run, UserIntervention
from app.schemas.run import PlanDecisionRequest, RunCreateRequest, RunOut, StepDecisionRequest
from app.services.agent.control import request_cancel, set_plan_decision, set_step_decision
from app.services.agent.events import redis_channel, replay_events

router = APIRouter(prefix="/api/runs", tags=["runs"])


async def _get_owned_run(run_id: str, user, db: AsyncSession) -> Run:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Запуск не знайдено")
    if run.user_id != user.id and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Немає доступу до цього запуску")
    return run


@router.post("", response_model=RunOut)
async def create_run(body: RunCreateRequest, user: CurrentUser, db: AsyncSession = Depends(get_db)) -> Run:
    project = await db.get(Project, body.project_id)
    if project is None or (project.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проєкт не знайдено")

    run = Run(
        project_id=body.project_id,
        user_id=user.id,
        task_text=body.task_text,
        mode=body.mode,
        status="queued",
        planner_endpoint_id=body.planner_endpoint_id,
        executor_endpoint_id=body.executor_endpoint_id,
        reasoning_level_planner=body.reasoning_level_planner,
        reasoning_level_executor=body.reasoning_level_executor,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    await enqueue_run(run.id)
    return run


@router.get("", response_model=list[RunOut])
async def list_runs(project_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)) -> list[Run]:
    project = await db.get(Project, project_id)
    if project is None or (project.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проєкт не знайдено")

    result = await db.execute(select(Run).where(Run.project_id == project_id).order_by(Run.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)) -> Run:
    return await _get_owned_run(run_id, user, db)


@router.post("/{run_id}/stop")
async def stop_run(run_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    await _get_owned_run(run_id, user, db)
    await request_cancel(get_redis(), run_id)
    return {"ok": True}


@router.post("/{run_id}/plan/decision")
async def decide_plan(
    run_id: str, body: PlanDecisionRequest, user: CurrentUser, db: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    run = await _get_owned_run(run_id, user, db)
    if run.status != "awaiting_plan_approval":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Запуск не очікує на затвердження плану")

    if body.steps is not None and body.steps != run.plan:
        db.add(
            UserIntervention(
                run_id=run_id,
                kind="plan_edit",
                payload={"before": run.plan, "after": body.steps},
                created_at=datetime.now(timezone.utc),
            )
        )
        run.plan = body.steps

    if body.decision == "reject":
        db.add(
            UserIntervention(
                run_id=run_id, kind="plan_reject", payload={}, created_at=datetime.now(timezone.utc)
            )
        )

    await db.commit()
    await set_plan_decision(get_redis(), run_id, body.decision)
    return {"ok": True}


@router.post("/{run_id}/step/decision")
async def decide_step(
    run_id: str, body: StepDecisionRequest, user: CurrentUser, db: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    run = await _get_owned_run(run_id, user, db)
    if run.status != "awaiting_step_approval":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Запуск не очікує на затвердження кроку")

    db.add(
        UserIntervention(
            run_id=run_id,
            kind=f"step_{body.decision}",
            payload={},
            created_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()
    await set_step_decision(get_redis(), run_id, body.decision)
    return {"ok": True}


def _sse_line(seq: int, event_type: str, payload: dict) -> str:
    return f"id: {seq}\nevent: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    since: int = -1,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    await _get_owned_run(run_id, user, db)
    start_seq = int(last_event_id) if last_event_id is not None else since

    async def generator():
        redis = get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(redis_channel(run_id))
        last_seq = start_seq
        try:
            already_finished = False
            for row in await replay_events(db, run_id, since_seq=last_seq):
                yield _sse_line(row.seq, row.type, row.payload)
                last_seq = row.seq
                if row.type in ("done", "error"):
                    already_finished = True

            while not already_finished:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if message is None:
                    yield ": keep-alive\n\n"
                    continue

                decoded = json.loads(message["data"])
                if decoded["seq"] <= last_seq:
                    continue
                last_seq = decoded["seq"]
                yield _sse_line(decoded["seq"], decoded["type"], decoded["payload"])
                if decoded["type"] in ("done", "error"):
                    break
        finally:
            await pubsub.unsubscribe(redis_channel(run_id))
            await pubsub.close()

    return StreamingResponse(generator(), media_type="text/event-stream")
