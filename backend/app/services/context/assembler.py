"""Builds the six-segment prompt (system / repo_map / pinned / retrieved /
summary / history) the agent loop (task 6) sends to a model endpoint, and
counts each segment's token cost via `context.counter` so both the
compactor's threshold check and the UI's context bar work off the exact
same numbers that went into the request.

Kept DB-agnostic like `services.projects.ingest`: callers hand in plain
file/text data already loaded from the database, so this module is
testable against fixtures with no session involved.
"""

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.models.endpoint import ModelEndpoint
from app.services.context.counter import ContextUsage, count_segments
from app.services.context.repomap import render_repo_map
from app.services.llm.toolcalls import ToolSpec, build_json_protocol_system_prompt
from app.services.projects.tree import FileEntry

Role = Literal["planner", "executor"]

PLANNER_SYSTEM_PROMPT = (
    "You are the planning half of a two-model coding agent. Given a task and "
    "the shape of a real codebase, produce a structured, ordered plan of "
    "concrete steps an executor model will carry out with file/shell tools. "
    "Each step should be small enough to verify on its own. Do not write the "
    "code yourself - describe what needs to change and why."
)

EXECUTOR_SYSTEM_PROMPT = (
    "You are the execution half of a two-model coding agent. You carry out "
    "one step of a plan at a time against a real project checked out on "
    "disk, using the available tools to read files, make changes, and run "
    "commands. Only act on the current step; report back clearly when it is "
    "done or blocked."
)


@dataclass(frozen=True)
class PinnedFile:
    entry: FileEntry
    content: str


@dataclass
class AssembledContext:
    messages: list[dict[str, Any]]
    usage: ContextUsage


def _format_file_block(path: str, content: str) -> str:
    return f"### {path}\n```\n{content}\n```"


def _build_system_text(role: Role, tools_mode: str, tool_specs: list[ToolSpec] | None) -> str:
    base = PLANNER_SYSTEM_PROMPT if role == "planner" else EXECUTOR_SYSTEM_PROMPT
    if tool_specs and tools_mode == "json_protocol":
        return f"{base}\n\n{build_json_protocol_system_prompt(tool_specs)}"
    return base


def _build_pinned_text(pinned_files: list[PinnedFile]) -> str:
    if not pinned_files:
        return ""
    blocks = "\n\n".join(_format_file_block(pf.entry.path, pf.content) for pf in pinned_files)
    return f"Pinned files (always kept in full):\n\n{blocks}"


def _build_retrieved_text(retrieved_files: dict[str, str]) -> str:
    if not retrieved_files:
        return ""
    blocks = "\n\n".join(_format_file_block(path, content) for path, content in retrieved_files.items())
    return f"Files read during this session (latest version only):\n\n{blocks}"


async def assemble_context(
    *,
    role: Role,
    all_files: list[FileEntry],
    pinned_files: list[PinnedFile],
    retrieved_files: dict[str, str],
    summary: str | None,
    history: list[dict[str, Any]],
    tool_specs: list[ToolSpec] | None,
    endpoint: ModelEndpoint,
    http_client: httpx.AsyncClient,
) -> AssembledContext:
    segment_texts = {
        "system": _build_system_text(role, endpoint.tools_mode, tool_specs),
        "repo_map": render_repo_map(all_files),
        "pinned": _build_pinned_text(pinned_files),
        "retrieved": _build_retrieved_text(retrieved_files),
        "summary": summary or "",
        "history": "\n".join(str(m.get("content", "")) for m in history),
    }

    usage = await count_segments(segment_texts, endpoint, http_client)

    messages: list[dict[str, Any]] = []
    for name in ("system", "repo_map", "pinned", "retrieved", "summary"):
        text = segment_texts[name]
        if text:
            messages.append({"role": "system", "content": text})
    messages.extend(history)

    return AssembledContext(messages=messages, usage=usage)
