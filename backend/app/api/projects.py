import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import CurrentUser
from app.models.project import Project, ProjectFile
from app.schemas.project import (
    FileContentOut,
    FilePinRequest,
    ProjectCreateRequest,
    ProjectOut,
    RepoMapOut,
    SnippetCreateRequest,
    TreeNodeOut,
    UploadResultOut,
)
from app.services.context.repomap import render_repo_map
from app.services.projects.ingest import IngestError, git_init_and_commit, write_files_to_workspace
from app.services.projects.tree import FileEntry, build_tree

router = APIRouter(prefix="/api/projects", tags=["projects"])
settings = get_settings()


def _workspace_path(project_id: str) -> Path:
    return Path(settings.projects_dir) / project_id / "workspace"


async def _get_owned_project(project_id: str, user, db: AsyncSession) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проєкт не знайдено")
    if project.owner_id != user.id and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Немає доступу до цього проєкту")
    return project


async def _persist_ingested_files(
    db: AsyncSession, project: Project, ingested: list, workspace_path: Path
) -> None:
    existing = (
        await db.execute(select(ProjectFile).where(ProjectFile.project_id == project.id))
    ).scalars()
    by_path = {f.path: f for f in existing}

    for item in ingested:
        row = by_path.get(item.path)
        if row is None:
            db.add(ProjectFile(project_id=project.id, path=item.path, size_bytes=item.size_bytes, token_count=item.token_count))
        else:
            row.size_bytes = item.size_bytes
            row.token_count = item.token_count

    await db.flush()

    all_files = (
        await db.execute(select(ProjectFile).where(ProjectFile.project_id == project.id))
    ).scalars().all()
    project.file_count = len(all_files)
    project.total_bytes = sum(f.size_bytes for f in all_files)


@router.post("", response_model=ProjectOut)
async def create_project(
    body: ProjectCreateRequest, user: CurrentUser, db: AsyncSession = Depends(get_db)
) -> Project:
    project = Project(owner_id=user.id, name=body.name, workspace_path="")
    db.add(project)
    await db.flush()

    workspace_path = _workspace_path(project.id)
    project.workspace_path = str(workspace_path)
    workspace_path.mkdir(parents=True, exist_ok=True)
    git_init_and_commit(workspace_path, "Empty project")

    await db.commit()
    await db.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
async def list_projects(user: CurrentUser, db: AsyncSession = Depends(get_db)) -> list[Project]:
    result = await db.execute(
        select(Project).where(Project.owner_id == user.id).order_by(Project.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)) -> Project:
    return await _get_owned_project(project_id, user, db)


@router.post("/{project_id}/upload", response_model=UploadResultOut)
async def upload_files(
    project_id: str,
    user: CurrentUser,
    files: list[UploadFile],
    db: AsyncSession = Depends(get_db),
) -> UploadResultOut:
    project = await _get_owned_project(project_id, user, db)
    workspace_path = _workspace_path(project.id)

    raw_files: list[tuple[str, bytes]] = []
    for upload in files:
        content = await upload.read()
        raw_files.append((upload.filename or "", content))

    try:
        result = write_files_to_workspace(
            workspace_path,
            raw_files,
            max_files=settings.max_project_files,
            max_total_bytes=settings.max_project_total_bytes,
            max_file_bytes=settings.max_project_file_bytes,
        )
    except IngestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    git_init_and_commit(workspace_path, "Import files")
    await _persist_ingested_files(db, project, result.files, workspace_path)
    await db.commit()
    await db.refresh(project)

    return UploadResultOut(project=project, ingested_count=len(result.files), skipped_binary=result.skipped_binary)


@router.post("/{project_id}/snippet", response_model=UploadResultOut)
async def upload_snippet(
    project_id: str,
    body: SnippetCreateRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> UploadResultOut:
    project = await _get_owned_project(project_id, user, db)
    workspace_path = _workspace_path(project.id)

    try:
        result = write_files_to_workspace(
            workspace_path,
            [(body.path, body.content.encode("utf-8"))],
            max_files=settings.max_project_files,
            max_total_bytes=settings.max_project_total_bytes,
            max_file_bytes=settings.max_project_file_bytes,
        )
    except IngestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    git_init_and_commit(workspace_path, f"Add {body.path}")
    await _persist_ingested_files(db, project, result.files, workspace_path)
    await db.commit()
    await db.refresh(project)

    return UploadResultOut(project=project, ingested_count=len(result.files), skipped_binary=result.skipped_binary)


@router.get("/{project_id}/tree", response_model=TreeNodeOut)
async def get_tree(project_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)) -> TreeNodeOut:
    project = await _get_owned_project(project_id, user, db)
    files = (
        await db.execute(select(ProjectFile).where(ProjectFile.project_id == project.id))
    ).scalars().all()

    entries = [
        FileEntry(id=f.id, path=f.path, size_bytes=f.size_bytes, token_count=f.token_count or 0, pinned=f.pinned)
        for f in files
    ]
    root = build_tree(entries)
    return _tree_node_to_schema(root)


def _tree_node_to_schema(node) -> TreeNodeOut:
    return TreeNodeOut(
        name=node.name,
        path=node.path,
        type=node.type,
        file_id=node.file_id,
        size_bytes=node.size_bytes,
        token_count=node.token_count,
        pinned=node.pinned,
        children=[_tree_node_to_schema(c) for c in node.children],
    )


@router.get("/{project_id}/repo-map", response_model=RepoMapOut)
async def get_repo_map(project_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)) -> RepoMapOut:
    project = await _get_owned_project(project_id, user, db)
    files = (
        await db.execute(select(ProjectFile).where(ProjectFile.project_id == project.id))
    ).scalars().all()

    entries = [
        FileEntry(id=f.id, path=f.path, size_bytes=f.size_bytes, token_count=f.token_count or 0, pinned=f.pinned)
        for f in files
    ]
    return RepoMapOut(text=render_repo_map(entries))


@router.get("/{project_id}/files/{file_id}/content", response_model=FileContentOut)
async def get_file_content(
    project_id: str, file_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)
) -> FileContentOut:
    project = await _get_owned_project(project_id, user, db)
    file_row = await db.get(ProjectFile, file_id)
    if file_row is None or file_row.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не знайдено")

    workspace_path = _workspace_path(project.id)
    target = (workspace_path / file_row.path).resolve()
    if workspace_path.resolve() not in target.parents or not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не знайдено на диску")

    content = target.read_bytes().decode("utf-8", errors="replace")
    return FileContentOut(path=file_row.path, content=content)


@router.patch("/{project_id}/files/{file_id}")
async def set_file_pinned(
    project_id: str,
    file_id: str,
    body: FilePinRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    project = await _get_owned_project(project_id, user, db)
    file_row = await db.get(ProjectFile, file_id)
    if file_row is None or file_row.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не знайдено")

    file_row.pinned = body.pinned
    await db.commit()
    return {"ok": True}


@router.delete("/{project_id}")
async def delete_project(project_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    project = await _get_owned_project(project_id, user, db)
    workspace_path = _workspace_path(project.id)

    await db.delete(project)
    await db.commit()

    shutil.rmtree(workspace_path.parent, ignore_errors=True)
    return {"ok": True}
