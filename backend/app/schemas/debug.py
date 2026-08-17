from typing import Any

from pydantic import BaseModel, Field


class McpCallIn(BaseModel):
    """Arbitrary tool call against a registered source, for checking a
    server's response shape after a deployment or an upgrade."""

    source: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class McpCallOut(BaseModel):
    is_error: bool = False
    structured: Any = None
    text: str = ""
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    # What the payload ladder made of it - the same value an adapter would
    # receive, which is the thing worth checking.
    parsed: Any = None
    error: str | None = None
