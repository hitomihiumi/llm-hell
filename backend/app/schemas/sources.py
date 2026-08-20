from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    kind: str
    display_name: str
    enabled: bool
    weight: float
    config: dict[str, Any] = Field(default_factory=dict)
    last_checked_at: datetime | None = None
    last_check_result: dict[str, Any] | None = None

    # Deliberately absent: `secret_ref`. It names an environment variable
    # rather than holding a credential, but publishing the names of an
    # installation's secrets to every logged-in user is free information for
    # no benefit.


class SourceUpdateIn(BaseModel):
    enabled: bool | None = None
    weight: float | None = Field(default=None, ge=0.0, le=10.0)
    config: dict[str, Any] | None = None


class SourceHealthOut(BaseModel):
    key: str
    ok: bool
    checked_at: datetime
    result: dict[str, Any] = Field(default_factory=dict)
