"""Turns a task description into a structured plan the executor works
through step by step. The plan itself travels as a JSON array in a fenced
```plan block - the same fenced-block-plus-parse pattern as the
json_protocol tool-call fallback (`services.llm.toolcalls`), just for a
plan instead of a tool call, since it needs the identical robustness
against a model wrapping its answer in prose.
"""

import json
import re
from dataclasses import dataclass

import httpx

from app.models.endpoint import ModelEndpoint
from app.services.context.assembler import assemble_context
from app.services.llm.client import ContentDelta, LLMClient, ReasoningDelta
from app.services.projects.tree import FileEntry

PLAN_INSTRUCTION = (
    "Respond with ONLY a single fenced code block, no other text, in this exact form:\n\n"
    "```plan\n"
    '[{"id": "step-1", "title": "...", "intent": "...", "files": ["..."], "done_when": "..."}]\n'
    "```\n\n"
    "Break the task into a small number of concrete, independently-verifiable steps. "
    "`files` lists the files each step is expected to touch. `done_when` is a concrete, "
    "checkable condition for the step being complete."
)


class PlanParseError(Exception):
    pass


@dataclass(frozen=True)
class PlanStep:
    id: str
    title: str
    intent: str
    files: list[str]
    done_when: str


@dataclass(frozen=True)
class GeneratedPlan:
    steps: list[PlanStep]
    reasoning: str


_PLAN_BLOCK_RE = re.compile(r"```plan\s*(\[.*?\])\s*```", re.DOTALL)


def parse_plan_response(content: str) -> list[PlanStep]:
    if not content:
        raise PlanParseError("planner returned an empty response")

    match = _PLAN_BLOCK_RE.search(content)
    raw = match.group(1) if match else content.strip()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanParseError(f"planner did not return valid JSON: {exc}") from exc

    if not isinstance(payload, list) or not payload:
        raise PlanParseError("planner response was not a non-empty JSON array")

    steps: list[PlanStep] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise PlanParseError(f"plan step {index} is not an object")
        if "title" not in item:
            raise PlanParseError(f"plan step {index} is missing 'title'")
        steps.append(
            PlanStep(
                id=str(item.get("id") or f"step-{index + 1}"),
                title=str(item["title"]),
                intent=str(item.get("intent", "")),
                files=[str(f) for f in (item.get("files") or [])],
                done_when=str(item.get("done_when", "")),
            )
        )

    return steps


async def generate_plan(
    *,
    task_text: str,
    all_files: list[FileEntry],
    endpoint: ModelEndpoint,
    reasoning_level: str,
    http_client: httpx.AsyncClient,
) -> GeneratedPlan:
    context = await assemble_context(
        role="planner",
        all_files=all_files,
        pinned_files=[],
        retrieved_files={},
        summary=None,
        history=[{"role": "user", "content": f"{task_text}\n\n{PLAN_INSTRUCTION}"}],
        tool_specs=None,
        endpoint=endpoint,
        http_client=http_client,
    )

    client = LLMClient(endpoint, http_client=http_client)
    content = ""
    reasoning = ""
    async for event in client.stream_chat(messages=context.messages, reasoning_level=reasoning_level):
        if isinstance(event, ContentDelta):
            content += event.text
        elif isinstance(event, ReasoningDelta):
            reasoning += event.text

    steps = parse_plan_response(content)
    return GeneratedPlan(steps=steps, reasoning=reasoning)
