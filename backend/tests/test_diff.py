from pathlib import Path

from app.services.projects.diff import commit_changes, get_changed_files, get_unified_diff
from app.services.projects.ingest import git_init_and_commit, write_files_to_workspace


def test_get_unified_diff_reflects_workspace_changes(tmp_path: Path) -> None:
    write_files_to_workspace(
        tmp_path, [("main.py", b"print(1)\n")], max_files=10, max_total_bytes=1000, max_file_bytes=1000
    )
    git_init_and_commit(tmp_path)

    assert get_unified_diff(tmp_path) == ""
    assert get_changed_files(tmp_path) == []

    (tmp_path / "main.py").write_text("print(2)\n")

    diff_text = get_unified_diff(tmp_path)
    assert "-print(1)" in diff_text
    assert "+print(2)" in diff_text
    assert get_changed_files(tmp_path) == ["main.py"]


def test_commit_changes_resets_the_diff_baseline(tmp_path: Path) -> None:
    write_files_to_workspace(
        tmp_path, [("main.py", b"print(1)\n")], max_files=10, max_total_bytes=1000, max_file_bytes=1000
    )
    git_init_and_commit(tmp_path)

    (tmp_path / "main.py").write_text("print(2)\n")
    commit_changes(tmp_path, "agent run 1")

    assert get_unified_diff(tmp_path) == ""
