"""Runs one plan step: a loop of executor-model turns and tool calls
against the sandboxed workspace, until the model calls `finish_step`, a
limit is hit, or the run is stopped. `history` and `retrieved_files` are
mutated in place (both already just plain mutable containers) so the
caller - `services.agent.loop` - keeps holding the single source of truth
for the whole run's accumulated context across steps, rather than this
function returning a second copy for the caller to merge back in.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.endpoint import ModelEndpoint
from app.models.run import Run
from app.services.agent.control import is_cancelled, wait_for_step_decision
from app.services.agent.events import Emitter
from app.services.agent.planner import PlanStep
from app.services.agent.tools import ALL_TOOLS, execute_tool_call
from app.services.context.assembler import PinnedFile, assemble_context
from app.services.llm.client import (
    ContentDelta,
    FinishEvent,
    LLMClient,
    ReasoningDelta,
    ToolCallComplete,
    UsageInfo,
)
from app.services.llm.toolcalls import ToolSpec, parse_json_protocol_response
from app.services.projects.tree import FileEntry
from app.services.sandbox.base import SandboxHandle

SIDE_EFFECT_TOOLS = {"write_file", "run_command", "run_tests"}

StepStatus = Literal["done", "blocked", "exhausted", "cancelled"]


@dataclass(frozen=True)
class StepOutcome:
    status: StepStatus
    summary: str
    iterations_used: int


def _step_prompt(step: PlanStep) -> str:
    files_hint = f" Expected to touch: {', '.join(step.files)}." if step.files else ""
    return (
        f"Current plan step: {step.title}\n"
        f"Intent: {step.intent}\n"
        f"Done when: {step.done_when}{files_hint}\n\n"
        "Work this step using the available tools. Call finish_step once the "
        "done_when condition is met, or if the step turns out to be blocked."
    )


def _refresh_retrieved_file(path: str, workspace_path: Path, retrieved_files: dict[str, str]) -> None:
    target = workspace_path / path
    if target.is_file():
        retrieved_files[path] = target.read_text(encoding="utf-8", errors="replace")


async def run_executor_step(
    *,
    step: PlanStep,
    run: Run,
    db: AsyncSession,
    redis: Redis,
    emitter: Emitter,
    all_files: list[FileEntry],
    pinned_files: list[PinnedFile],
    retrieved_files: dict[str, str],
    summary: str | None,
    history: list[dict[str, Any]],
    endpoint: ModelEndpoint,
    reasoning_level: str,
    workspace_path: Path,
    sandbox: SandboxHandle | None,
    command_timeout_seconds: int,
    max_iterations: int,
    http_client: httpx.AsyncClient,
    step_approval_timeout_seconds: float = 1800,
    tools: list[ToolSpec] | None = None,
) -> StepOutcome:
    tools = tools if tools is not None else ALL_TOOLS
    history.append({"role": "user", "content": _step_prompt(step)})

    for iteration in range(max_iterations):
        if await is_cancelled(redis, run.id):
            return StepOutcome("cancelled", "Run was stopped by the user.", iteration)

        context = await assemble_context(
            role="executor",
            all_files=all_files,
            pinned_files=pinned_files,
            retrieved_files=retrieved_files,
            summary=summary,
            history=history,
            tool_specs=tools,
            endpoint=endpoint,
            http_client=http_client,
        )
        await emitter.emit("context_update", context.usage.to_dict())

        client = LLMClient(endpoint, http_client=http_client)
        content = ""
        reasoning = ""
        tool_call: ToolCallComplete | None = None

        async for event in client.stream_chat(
            messages=context.messages, reasoning_level=reasoning_level, tools=tools
        ):
            if isinstance(event, ContentDelta):
                content += event.text
                await emitter.emit("token", {"text": event.text})
            elif isinstance(event, ReasoningDelta):
                reasoning += event.text
                await emitter.emit("reasoning", {"text": event.text})
            elif isinstance(event, ToolCallComplete):
                tool_call = event
            elif isinstance(event, UsageInfo):
                await emitter.emit(
                    "usage", {"prompt_tokens": event.prompt_tokens, "completion_tokens": event.completion_tokens}
                )
            elif isinstance(event, FinishEvent):
                pass

        if tool_call is None and endpoint.tools_mode == "json_protocol":
            parsed = parse_json_protocol_response(content)
            if parsed is not None:
                tool_call = ToolCallComplete(
                    call_id=f"json-{iteration}", name=parsed.name, arguments=parsed.arguments, raw_arguments=""
                )

        assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
        if reasoning:
            assistant_message["reasoning"] = reasoning
        history.append(assistant_message)

        if tool_call is None:
            # The model talked instead of acting - give it another turn
            # rather than treating a single miss as fatal.
            continue

        await emitter.emit("tool_call_start", {"tool": tool_call.name, "arguments": tool_call.arguments})

        if tool_call.name in SIDE_EFFECT_TOOLS and run.mode == "stepwise":
            run.status = "awaiting_step_approval"
            await db.flush()
            decision = await wait_for_step_decision(
                redis, run.id, max_wait_seconds=step_approval_timeout_seconds
            )
            run.status = "running"
            await db.flush()

            if decision in ("cancelled", None):
                await emitter.emit("tool_call_end", {"tool": tool_call.name, "ok": False, "result": "not approved in time"})
                return StepOutcome("cancelled", "Step approval was cancelled or timed out.", iteration)
            if decision == "reject":
                history.append({"role": "tool", "content": "This action was rejected by the user."})
                await emitter.emit("tool_call_end", {"tool": tool_call.name, "ok": False, "result": "rejected"})
                continue

        result = await execute_tool_call(
            tool_call.name,
            tool_call.arguments,
            workspace_path=workspace_path,
            sandbox=sandbox,
            command_timeout_seconds=command_timeout_seconds,
        )
        await emitter.emit("tool_call_end", {"tool": result.tool_name, "ok": result.ok, "result": result.content})

        if result.is_finish_signal:
            return StepOutcome(result.finish_status or "done", result.content, iteration + 1)

        if result.ok and tool_call.name in ("read_file", "write_file"):
            path = tool_call.arguments.get("path")
            if isinstance(path, str):
                _refresh_retrieved_file(path, workspace_path, retrieved_files)

        history.append({"role": "tool", "content": result.content})

    return StepOutcome("exhausted", f"Step did not finish within {max_iterations} iterations.", max_iterations)
