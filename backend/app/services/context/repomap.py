"""Builds the textual `repo_map` prompt segment: a compact directory tree
with per-file token counts, so the model sees the whole project's shape
without every file's full content being in context.

v1 is a plain tree + size listing. Extracting top-level symbol
signatures for large files (so the map stays useful without full content
for files too big to inline) is a context-budget optimization that
belongs with the rest of the assembler/compactor work, not here.
"""

from app.services.projects.tree import FileEntry, TreeNode, build_tree


def render_repo_map(files: list[FileEntry]) -> str:
    if not files:
        return "(empty project)"

    root = build_tree(files)
    total_tokens = sum(f.token_count for f in files)
    lines = [f"Project files ({len(files)} files, ~{total_tokens:,} tokens):"]
    _render_node(root, depth=0, lines=lines)
    return "\n".join(lines)


def _render_node(node: TreeNode, depth: int, lines: list[str]) -> None:
    indent = "  " * depth
    for child in node.children:
        if child.type == "dir":
            lines.append(f"{indent}{child.name}/")
            _render_node(child, depth + 1, lines)
        else:
            marker = " [pinned]" if child.pinned else ""
            lines.append(f"{indent}{child.name} (~{child.token_count:,} tokens){marker}")
