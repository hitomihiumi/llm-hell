from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ProjectOut(BaseModel):
    id: str
    name: str
    file_count: int
    total_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ProjectCreateRequest(BaseModel):
    name: str


class SnippetCreateRequest(BaseModel):
    path: str
    content: str


class UploadResultOut(BaseModel):
    project: ProjectOut
    ingested_count: int
    skipped_binary: list[str]


class FilePinRequest(BaseModel):
    pinned: bool


class TreeNodeOut(BaseModel):
    name: str
    path: str
    type: Literal["file", "dir"]
    file_id: str | None = None
    size_bytes: int = 0
    token_count: int = 0
    pinned: bool = False
    children: list["TreeNodeOut"] = []


class RepoMapOut(BaseModel):
    text: str


class FileContentOut(BaseModel):
    path: str
    content: str
