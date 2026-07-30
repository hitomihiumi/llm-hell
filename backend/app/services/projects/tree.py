"""Turns a flat list of project files into a nested tree for the
frontend's file explorer. Takes plain `FileEntry` values rather than
SQLAlchemy rows so it stays testable without a database.
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class FileEntry:
    id: str
    path: str  # posix-style, relative to the workspace root
    size_bytes: int
    token_count: int
    pinned: bool


@dataclass
class TreeNode:
    name: str
    path: str
    type: Literal["file", "dir"]
    file_id: str | None = None
    size_bytes: int = 0
    token_count: int = 0
    pinned: bool = False
    children: list["TreeNode"] = field(default_factory=list)


def build_tree(files: list[FileEntry]) -> TreeNode:
    root = TreeNode(name="", path="", type="dir")
    dirs: dict[str, TreeNode] = {"": root}

    def get_dir(path: str) -> TreeNode:
        if path in dirs:
            return dirs[path]
        parent_path, _, name = path.rpartition("/")
        parent = get_dir(parent_path)
        node = TreeNode(name=name, path=path, type="dir")
        parent.children.append(node)
        dirs[path] = node
        return node

    for entry in files:
        parent_path, _, name = entry.path.rpartition("/")
        parent = get_dir(parent_path)
        parent.children.append(
            TreeNode(
                name=name,
                path=entry.path,
                type="file",
                file_id=entry.id,
                size_bytes=entry.size_bytes,
                token_count=entry.token_count,
                pinned=entry.pinned,
            )
        )

    _sort_recursive(root)
    return root


def _sort_recursive(node: TreeNode) -> None:
    node.children.sort(key=lambda n: (n.type != "dir", n.name.lower()))
    for child in node.children:
        if child.type == "dir":
            _sort_recursive(child)
