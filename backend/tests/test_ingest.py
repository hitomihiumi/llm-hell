from pathlib import Path

import pytest

from app.services.projects.ingest import (
    IngestError,
    git_init_and_commit,
    looks_binary,
    sanitize_relative_path,
    write_files_to_workspace,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("src/main.py", "src/main.py"),
        ("src\\main.py", "src/main.py"),
        ("/src/main.py", "src/main.py"),
        ("./src/./main.py", "src/main.py"),
    ],
)
def test_sanitize_relative_path_accepts_normal_paths(raw: str, expected: str) -> None:
    assert sanitize_relative_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "../escape.py",
        "src/../../escape.py",
        "..",
        "C:/Windows/system32/evil.dll",
        "",
        "a\x00b",
    ],
)
def test_sanitize_relative_path_rejects_unsafe_paths(raw: str) -> None:
    assert sanitize_relative_path(raw) is None


def test_looks_binary_detects_nul_byte() -> None:
    assert looks_binary(b"hello\x00world")
    assert not looks_binary(b"just plain text")


def test_write_files_to_workspace_writes_text_and_skips_binary(tmp_path: Path) -> None:
    files = [
        ("src/main.py", b"print('hi')\n"),
        ("assets/logo.png", b"\x89PNG\x00\x00binarydata"),
        ("README.md", b"# hello\n"),
    ]

    result = write_files_to_workspace(
        tmp_path, files, max_files=100, max_total_bytes=10_000, max_file_bytes=1_000
    )

    assert {f.path for f in result.files} == {"src/main.py", "README.md"}
    assert result.skipped_binary == ["assets/logo.png"]
    assert (tmp_path / "src" / "main.py").read_bytes() == b"print('hi')\n"
    assert not (tmp_path / "assets" / "logo.png").exists()


def test_write_files_to_workspace_rejects_unsafe_path(tmp_path: Path) -> None:
    with pytest.raises(IngestError):
        write_files_to_workspace(
            tmp_path, [("../escape.py", b"x")], max_files=10, max_total_bytes=1000, max_file_bytes=100
        )


def test_write_files_to_workspace_enforces_per_file_size_limit(tmp_path: Path) -> None:
    with pytest.raises(IngestError):
        write_files_to_workspace(
            tmp_path, [("big.py", b"x" * 200)], max_files=10, max_total_bytes=1000, max_file_bytes=100
        )


def test_write_files_to_workspace_enforces_total_size_limit(tmp_path: Path) -> None:
    files = [("a.py", b"x" * 60), ("b.py", b"x" * 60)]
    with pytest.raises(IngestError):
        write_files_to_workspace(tmp_path, files, max_files=10, max_total_bytes=100, max_file_bytes=1000)


def test_write_files_to_workspace_enforces_file_count_limit(tmp_path: Path) -> None:
    files = [(f"f{i}.py", b"x") for i in range(5)]
    with pytest.raises(IngestError):
        write_files_to_workspace(tmp_path, files, max_files=3, max_total_bytes=1000, max_file_bytes=100)


def test_git_init_and_commit_creates_a_repo_with_one_commit(tmp_path: Path) -> None:
    write_files_to_workspace(
        tmp_path, [("main.py", b"print(1)\n")], max_files=10, max_total_bytes=1000, max_file_bytes=1000
    )
    git_init_and_commit(tmp_path)

    assert (tmp_path / ".git").is_dir()

    import subprocess

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert len(log.stdout.strip().splitlines()) == 1


def test_git_init_and_commit_is_safe_on_empty_workspace(tmp_path: Path) -> None:
    git_init_and_commit(tmp_path)  # should not raise even with nothing to commit
    assert (tmp_path / ".git").is_dir()
