from app.services.context.repomap import render_repo_map
from app.services.projects.tree import FileEntry, build_tree

FILES = [
    FileEntry(id="1", path="src/main.py", size_bytes=100, token_count=40, pinned=True),
    FileEntry(id="2", path="src/utils/helpers.py", size_bytes=50, token_count=20, pinned=False),
    FileEntry(id="3", path="README.md", size_bytes=30, token_count=10, pinned=False),
]


def test_build_tree_nests_directories_correctly() -> None:
    root = build_tree(FILES)

    assert {c.name for c in root.children} == {"src", "README.md"}
    src_node = next(c for c in root.children if c.name == "src")
    assert {c.name for c in src_node.children} == {"main.py", "utils"}

    utils_node = next(c for c in src_node.children if c.name == "utils")
    assert utils_node.children[0].name == "helpers.py"
    assert utils_node.children[0].path == "src/utils/helpers.py"


def test_build_tree_marks_pinned_files() -> None:
    root = build_tree(FILES)
    src_node = next(c for c in root.children if c.name == "src")
    main_node = next(c for c in src_node.children if c.name == "main.py")
    assert main_node.pinned is True
    assert main_node.token_count == 40


def test_build_tree_sorts_dirs_before_files_alphabetically() -> None:
    root = build_tree(FILES)
    names = [c.name for c in root.children]
    assert names == ["src", "README.md"]  # dirs first, then files


def test_render_repo_map_includes_counts_and_pinned_marker() -> None:
    text = render_repo_map(FILES)
    assert "3 files" in text
    assert "~70 tokens" in text  # 40 + 20 + 10
    assert "main.py (~40 tokens) [pinned]" in text
    assert "helpers.py (~20 tokens)" in text


def test_render_repo_map_handles_empty_project() -> None:
    assert render_repo_map([]) == "(empty project)"
