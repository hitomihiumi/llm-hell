"""Reads back what an agent run changed in a project's workspace, using
the git repo `ingest.git_init_and_commit` set up at import time. Kept as
a thin subprocess wrapper so the diff/patch views (task: UI polish) and
run-outcome bookkeeping can both call into it without shelling out
themselves.
"""

import subprocess
from pathlib import Path


def get_unified_diff(workspace_path: Path) -> str:
    """Unstaged + staged changes against the last commit, unified diff text."""
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=workspace_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def get_changed_files(workspace_path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=workspace_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line]


def commit_changes(workspace_path: Path, message: str) -> None:
    """Snapshots the current workspace state as a new commit, so a later
    diff can isolate what changed *after* this point (e.g. per agent run)."""
    subprocess.run(["git", "add", "-A"], cwd=workspace_path, capture_output=True, text=True, check=False)
    subprocess.run(
        ["git", "commit", "-q", "-m", message, "--allow-empty"],
        cwd=workspace_path,
        capture_output=True,
        text=True,
        check=False,
    )
