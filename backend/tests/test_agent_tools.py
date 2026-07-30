from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.agent.tools import ALL_TOOLS, execute_tool_call
from app.services.sandbox.base import ExecResult, SandboxHandle


class FakeSandboxHandle(SandboxHandle):
    def __init__(self, result: ExecResult | None = None):
        self.calls: list[dict] = []
        self._result = result or ExecResult(
            command="", exit_code=0, stdout="ok\n", stderr="", timed_out=False, truncated=False
        )

    async def exec(self, command, *, timeout=None, max_output_lines=2000):
        self.calls.append({"command": command, "timeout": timeout})
        return self._result

    async def stop(self) -> None:
        pass

    @property
    def idle_seconds(self) -> float:
        return 0.0


def test_all_tools_have_unique_names() -> None:
    names = [t.name for t in ALL_TOOLS]
    assert len(names) == len(set(names))


@pytest.mark.asyncio
async def test_read_file_returns_full_content(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("line1\nline2\nline3\n")
    result = await execute_tool_call(
        "read_file", {"path": "main.py"}, workspace_path=tmp_path, sandbox=None, command_timeout_seconds=30
    )
    assert result.ok
    assert result.content == "line1\nline2\nline3"


@pytest.mark.asyncio
async def test_read_file_respects_line_range(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("\n".join(f"line{i}" for i in range(1, 11)))
    result = await execute_tool_call(
        "read_file",
        {"path": "main.py", "start_line": 3, "end_line": 5},
        workspace_path=tmp_path,
        sandbox=None,
        command_timeout_seconds=30,
    )
    assert result.content == "line3\nline4\nline5"


@pytest.mark.asyncio
async def test_read_file_missing_file_is_an_error(tmp_path: Path) -> None:
    result = await execute_tool_call(
        "read_file", {"path": "nope.py"}, workspace_path=tmp_path, sandbox=None, command_timeout_seconds=30
    )
    assert not result.ok
    assert "no such file" in result.content


@pytest.mark.asyncio
async def test_read_file_rejects_path_traversal(tmp_path: Path) -> None:
    result = await execute_tool_call(
        "read_file", {"path": "../escape.py"}, workspace_path=tmp_path, sandbox=None, command_timeout_seconds=30
    )
    assert not result.ok
    assert "unsafe" in result.content


@pytest.mark.asyncio
async def test_list_dir_lists_files_and_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x")
    (tmp_path / "README.md").write_text("x")

    result = await execute_tool_call(
        "list_dir", {"path": "."}, workspace_path=tmp_path, sandbox=None, command_timeout_seconds=30
    )
    assert result.ok
    assert "dir   src" in result.content
    assert "file  README.md" in result.content


@pytest.mark.asyncio
async def test_list_dir_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    result = await execute_tool_call(
        "list_dir", {"path": "empty"}, workspace_path=tmp_path, sandbox=None, command_timeout_seconds=30
    )
    assert result.content == "(empty directory)"


@pytest.mark.asyncio
async def test_grep_finds_matches(tmp_path: Path, monkeypatch) -> None:
    def fake_run(cmd, cwd, capture_output, text, timeout):
        return SimpleNamespace(returncode=0, stdout="main.py:1:def main():\n", stderr="")

    monkeypatch.setattr("app.services.agent.tools.subprocess.run", fake_run)
    result = await execute_tool_call(
        "grep", {"pattern": "def main"}, workspace_path=tmp_path, sandbox=None, command_timeout_seconds=30
    )
    assert result.ok
    assert "main.py:1" in result.content


@pytest.mark.asyncio
async def test_grep_no_matches(tmp_path: Path, monkeypatch) -> None:
    def fake_run(cmd, cwd, capture_output, text, timeout):
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr("app.services.agent.tools.subprocess.run", fake_run)
    result = await execute_tool_call(
        "grep", {"pattern": "notfound_xyz"}, workspace_path=tmp_path, sandbox=None, command_timeout_seconds=30
    )
    assert result.ok
    assert result.content == "(no matches)"


@pytest.mark.asyncio
async def test_grep_reports_ripgrep_failure(tmp_path: Path, monkeypatch) -> None:
    def fake_run(cmd, cwd, capture_output, text, timeout):
        return SimpleNamespace(returncode=2, stdout="", stderr="regex parse error")

    monkeypatch.setattr("app.services.agent.tools.subprocess.run", fake_run)
    result = await execute_tool_call(
        "grep", {"pattern": "(unclosed"}, workspace_path=tmp_path, sandbox=None, command_timeout_seconds=30
    )
    assert not result.ok
    assert "regex parse error" in result.content


@pytest.mark.asyncio
async def test_write_file_creates_parent_dirs(tmp_path: Path) -> None:
    result = await execute_tool_call(
        "write_file",
        {"path": "src/new_module.py", "content": "print(1)\n"},
        workspace_path=tmp_path,
        sandbox=None,
        command_timeout_seconds=30,
    )
    assert result.ok
    assert (tmp_path / "src" / "new_module.py").read_text() == "print(1)\n"


@pytest.mark.asyncio
async def test_run_command_without_sandbox_is_an_error(tmp_path: Path) -> None:
    result = await execute_tool_call(
        "run_command", {"command": "echo hi"}, workspace_path=tmp_path, sandbox=None, command_timeout_seconds=30
    )
    assert not result.ok
    assert "no sandbox" in result.content


@pytest.mark.asyncio
async def test_run_command_uses_sandbox_exec(tmp_path: Path) -> None:
    sandbox = FakeSandboxHandle(
        ExecResult(command="", exit_code=0, stdout="all good\n", stderr="", timed_out=False, truncated=False)
    )
    result = await execute_tool_call(
        "run_command", {"command": "pytest -q"}, workspace_path=tmp_path, sandbox=sandbox, command_timeout_seconds=45
    )
    assert result.ok
    assert "all good" in result.content
    assert sandbox.calls[0]["timeout"] == 45
    assert "pytest -q" in sandbox.calls[0]["command"][-1]


@pytest.mark.asyncio
async def test_run_tests_reports_timeout(tmp_path: Path) -> None:
    sandbox = FakeSandboxHandle(
        ExecResult(command="", exit_code=137, stdout="", stderr="", timed_out=True, truncated=False)
    )
    result = await execute_tool_call(
        "run_tests", {"command": "pytest"}, workspace_path=tmp_path, sandbox=sandbox, command_timeout_seconds=1
    )
    assert result.ok  # the tool call itself succeeded; the command inside timed out
    assert "timed out" in result.content


@pytest.mark.asyncio
async def test_finish_step_is_a_signal_not_an_execution(tmp_path: Path) -> None:
    result = await execute_tool_call(
        "finish_step",
        {"status": "done", "summary": "Renamed the function and updated callers."},
        workspace_path=tmp_path,
        sandbox=None,
        command_timeout_seconds=30,
    )
    assert result.is_finish_signal
    assert result.finish_status == "done"
    assert result.content == "Renamed the function and updated callers."


@pytest.mark.asyncio
async def test_unknown_tool_name_is_an_error(tmp_path: Path) -> None:
    result = await execute_tool_call(
        "delete_everything", {}, workspace_path=tmp_path, sandbox=None, command_timeout_seconds=30
    )
    assert not result.ok
    assert "unknown tool" in result.content
