from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

Mode = Literal["autopilot", "approve_plan", "stepwise"]
ReasoningLevel = Literal["off", "low", "medium", "high"]


class RunCreateRequest(BaseModel):
    project_id: str
    task_text: str
    mode: Mode = "approve_plan"
    planner_endpoint_id: str
    executor_endpoint_id: str
    reasoning_level_planner: ReasoningLevel = "medium"
    reasoning_level_executor: ReasoningLevel = "off"


class RunOut(BaseModel):
    id: str
    project_id: str
    task_text: str
    mode: str
    status: str
    plan: list[dict[str, Any]] | None
    current_step_index: int
    tokens_prompt: int
    tokens_completion: int
    tokens_reasoning: int
    cost_estimate_usd: float
    stop_reason: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PlanDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    steps: list[dict[str, Any]] | None = None


class StepDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
