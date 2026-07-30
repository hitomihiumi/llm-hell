"""The concrete tools the executor model calls against a project's
workspace. Read/list/grep/write operate directly on the host filesystem
(the same directory the sandbox container has bind-mounted, per the
plan's DooD note) since they don't need isolation; only `run_command` and
`run_tests` go through the `SandboxBackend.exec()` boundary from task 5.

`write_file` stands in for the plan's "apply_patch/write_file" pairing:
having the executor emit full file contents is far more robust against a
model's own formatting mistakes than having it produce a unified diff for
us to apply, at the cost of using more output tokens per edit.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.projects.ingest import IngestError, sanitize_relative_path
from app.services.sandbox.base import SandboxHandle
from app.services.llm.toolcalls import ToolSpec

READ_FILE = ToolSpec(
    name="read_file",
    description="Read a text file from the project workspace, optionally a line range.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace root."},
            "start_line": {"type": "integer", "description": "1-indexed, inclusive. Omit to read from the start."},
            "end_line": {"type": "integer", "description": "1-indexed, inclusive. Omit to read to the end."},
        },
        "required": ["path"],
    },
)

LIST_DIR = ToolSpec(
    name="list_dir",
    description="List files and subdirectories at a path in the project workspace.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path relative to the workspace root, '.' for the root."}},
        "required": ["path"],
    },
)

GREP = ToolSpec(
    name="grep",
    description="Search the project workspace for a regex pattern using ripgrep.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Restrict the search to this path. Omit to search everything."},
        },
        "required": ["pattern"],
    },
)

WRITE_FILE = ToolSpec(
    name="write_file",
    description="Create or overwrite a text file in the project workspace with the given content.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace root."},
            "content": {"type": "string", "description": "The complete new content of the file."},
        },
        "required": ["path", "content"],
    },
)

RUN_COMMAND = ToolSpec(
    name="run_command",
    description="Run a shell command in the sandboxed workspace and return its output.",
    parameters={
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
)

RUN_TESTS = ToolSpec(
    name="run_tests",
    description="Run the project's test command in the sandboxed workspace and return its output.",
    parameters={
        "type": "object",
        "properties": {"command": {"type": "string", "description": "The test command, e.g. 'pytest -q'."}},
        "required": ["command"],
    },
)

FINISH_STEP = ToolSpec(
    name="finish_step",
    description="Signal that the current plan step is finished (or permanently blocked). Call this once the step's done_when condition is met.",
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["done", "blocked"]},
            "summary": {"type": "string", "description": "One or two sentences on what was done or why it's blocked."},
        },
        "required": ["status", "summary"],
    },
)

ALL_TOOLS = [READ_FILE, LIST_DIR, GREP, WRITE_FILE, RUN_COMMAND, RUN_TESTS, FINISH_STEP]

_MAX_TOOL_OUTPUT_CHARS = 20_000


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_name: str
    ok: bool
    content: str  # fed back to the model as the tool result
    is_finish_signal: bool = False
    finish_status: str | None = None  # "done" | "blocked", only set when is_finish_signal


class ToolError(Exception):
    pass


def _resolve_path(workspace_path: Path, raw_path: str) -> Path:
    safe = sanitize_relative_path(raw_path)
    if safe is None:
        raise ToolError(f"unsafe or invalid path: {raw_path!r}")
    return workspace_path / safe


def _clip(text: str) -> str:
    if len(text) <= _MAX_TOOL_OUTPUT_CHARS:
        return text
    return text[:_MAX_TOOL_OUTPUT_CHARS] + f"\n... [clipped after {_MAX_TOOL_OUTPUT_CHARS} characters]"


def _read_file(workspace_path: Path, arguments: dict[str, Any]) -> str:
    target = _resolve_path(workspace_path, arguments["path"])
    if not target.is_file():
        raise ToolError(f"no such file: {arguments['path']}")

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(arguments.get("start_line") or 1))
    end = min(len(lines), int(arguments.get("end_line") or len(lines)))
    selected = lines[start - 1 : end]
    return _clip("\n".join(selected))


def _list_dir(workspace_path: Path, arguments: dict[str, Any]) -> str:
    raw_path = arguments["path"]
    target = workspace_path if raw_path == "." else _resolve_path(workspace_path, raw_path)
    if not target.is_dir():
        raise ToolError(f"no such directory: {arguments['path']}")

    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    lines = [f"{'file' if e.is_file() else 'dir '}  {e.name}" for e in entries if e.name != ".git"]
    return _clip("\n".join(lines)) if lines else "(empty directory)"


_GREP_UNSAFE_PATTERN_CHARS = re.compile(r"[\x00]")


def _grep(workspace_path: Path, arguments: dict[str, Any]) -> str:
    pattern = arguments["pattern"]
    if _GREP_UNSAFE_PATTERN_CHARS.search(pattern):
        raise ToolError("invalid pattern")

    search_path = arguments.get("path") or "."
    safe_path = sanitize_relative_path(search_path) if search_path != "." else "."
    if safe_path is None:
        raise ToolError(f"unsafe or invalid path: {search_path!r}")

    result = subprocess.run(
        ["rg", "-n", "--no-heading", "--max-count", "50", pattern, safe_path],
        cwd=workspace_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode not in (0, 1):  # 1 == no matches, not an error
        raise ToolError(result.stderr.strip() or "grep failed")
    return _clip(result.stdout.strip()) if result.stdout.strip() else "(no matches)"


def _write_file(workspace_path: Path, arguments: dict[str, Any]) -> str:
    target = _resolve_path(workspace_path, arguments["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(arguments["content"], encoding="utf-8")
    return f"wrote {len(arguments['content'])} characters to {arguments['path']}"


async def _run_command(
    arguments: dict[str, Any], sandbox: SandboxHandle | None, command_timeout_seconds: int
) -> str:
    if sandbox is None:
        raise ToolError("no sandbox available to run commands in")

    result = await sandbox.exec(["sh", "-c", arguments["command"]], timeout=command_timeout_seconds)
    status = "timed out" if result.timed_out else f"exit code {result.exit_code}"
    return f"$ {arguments['command']}\n({status})\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"


async def execute_tool_call(
    name: str,
    arguments: dict[str, Any],
    *,
    workspace_path: Path,
    sandbox: SandboxHandle | None,
    command_timeout_seconds: int,
) -> ToolExecutionResult:
    try:
        if name == "read_file":
            return ToolExecutionResult(name, True, _read_file(workspace_path, arguments))
        if name == "list_dir":
            return ToolExecutionResult(name, True, _list_dir(workspace_path, arguments))
        if name == "grep":
            return ToolExecutionResult(name, True, _grep(workspace_path, arguments))
        if name == "write_file":
            return ToolExecutionResult(name, True, _write_file(workspace_path, arguments))
        if name in ("run_command", "run_tests"):
            content = await _run_command(arguments, sandbox, command_timeout_seconds)
            return ToolExecutionResult(name, True, content)
        if name == "finish_step":
            status = arguments.get("status", "done")
            summary = arguments.get("summary", "")
            return ToolExecutionResult(
                name, True, summary, is_finish_signal=True, finish_status=status
            )
        return ToolExecutionResult(name, False, f"unknown tool: {name}")
    except ToolError as exc:
        return ToolExecutionResult(name, False, f"error: {exc}")
    except Exception as exc:  # noqa: BLE001 - a tool failing must not crash the agent loop
        return ToolExecutionResult(name, False, f"unexpected error: {exc}")
