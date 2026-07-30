"""Writes an uploaded project's files into its on-disk workspace.

Deliberately DB-agnostic: takes raw (relative_path, content) pairs and
returns plain metadata, so the API router does the ORM persistence and
this module stays testable against a plain tmp directory with no
database involved. The client already filters out ignored/binary/oversize
files before upload (see the frontend import flow), but every limit is
re-enforced here too since the client can't be trusted as the only gate.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.services.llm.tokenizer import heuristic_token_count

_BINARY_SNIFF_BYTES = 8000


class IngestError(Exception):
    pass


@dataclass(frozen=True)
class IngestedFile:
    path: str  # posix-style, relative to the workspace root
    size_bytes: int
    token_count: int


@dataclass(frozen=True)
class IngestResult:
    files: list[IngestedFile]
    skipped_binary: list[str]


def sanitize_relative_path(raw_path: str) -> str | None:
    """Returns a safe posix-style relative path, or None if `raw_path`
    tries to escape the workspace (`..`, absolute, drive letter, NUL)."""
    if not raw_path or "\x00" in raw_path:
        return None

    normalized = raw_path.replace("\\", "/").lstrip("/")
    if len(normalized) >= 2 and normalized[1] == ":":  # drive letter, e.g. "C:/..."
        return None

    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None

    return "/".join(parts)


def looks_binary(content: bytes) -> bool:
    return b"\x00" in content[:_BINARY_SNIFF_BYTES]


def write_files_to_workspace(
    workspace_path: Path,
    files: list[tuple[str, bytes]],
    max_files: int,
    max_total_bytes: int,
    max_file_bytes: int,
) -> IngestResult:
    if len(files) > max_files:
        raise IngestError(f"too many files: {len(files)} exceeds the limit of {max_files}")

    workspace_path.mkdir(parents=True, exist_ok=True)

    ingested: list[IngestedFile] = []
    skipped_binary: list[str] = []
    total_bytes = 0

    for raw_path, content in files:
        safe_path = sanitize_relative_path(raw_path)
        if safe_path is None:
            raise IngestError(f"unsafe file path rejected: {raw_path!r}")

        if len(content) > max_file_bytes:
            raise IngestError(f"file too large: {safe_path} ({len(content)} bytes > {max_file_bytes})")

        if looks_binary(content):
            skipped_binary.append(safe_path)
            continue

        total_bytes += len(content)
        if total_bytes > max_total_bytes:
            raise IngestError(f"project exceeds the total size limit of {max_total_bytes} bytes")

        target = workspace_path / safe_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

        text = content.decode("utf-8", errors="replace")
        ingested.append(
            IngestedFile(path=safe_path, size_bytes=len(content), token_count=heuristic_token_count(text))
        )

    return IngestResult(files=ingested, skipped_binary=skipped_binary)


def _run_git(workspace_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=workspace_path, capture_output=True, text=True, check=False
    )


def git_init_and_commit(workspace_path: Path, message: str = "Initial import") -> None:
    """Initializes a git repo in the workspace and commits its current
    contents, giving the rest of the app free `git diff` / patch export
    for anything the agent changes later."""
    init = _run_git(workspace_path, "init", "-q")
    if init.returncode != 0:
        raise IngestError(f"git init failed: {init.stderr}")

    _run_git(workspace_path, "config", "user.email", "llmhell@localhost")
    _run_git(workspace_path, "config", "user.name", "LLM-Hell")
    _run_git(workspace_path, "add", "-A")

    commit = _run_git(workspace_path, "commit", "-q", "-m", message)
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
        raise IngestError(f"git commit failed: {commit.stderr}")
