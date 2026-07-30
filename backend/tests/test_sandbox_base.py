from app.services.sandbox.base import workspace_host_path


def test_workspace_host_path_joins_project_and_workspace() -> None:
    assert workspace_host_path("proj-1", "/srv/llmhell/projects") == "/srv/llmhell/projects/proj-1/workspace"


def test_workspace_host_path_normalizes_relative_dir() -> None:
    assert workspace_host_path("proj-1", "./data/projects") == "data/projects/proj-1/workspace"
