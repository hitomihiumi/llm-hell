from typing import Any

from pydantic import BaseModel, Field


class RecordOut(BaseModel):
    """One knowledge-base row, behind a synthesised `/records/{table}/{pk}`
    link. `table` is echoed back only after it matched the whitelist."""

    table: str
    pk: str
    fields: dict[str, Any] = Field(default_factory=dict)
