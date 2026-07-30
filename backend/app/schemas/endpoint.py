from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["planner", "executor"]
ToolsMode = Literal["native", "json_protocol"]


class EndpointCreateRequest(BaseModel):
    name: str
    base_url: str
    api_key: str | None = None
    model_id: str
    role: Role
    ctx_window: int = 32768
    price_per_mtok_in: float = 0.0
    price_per_mtok_out: float = 0.0
    tools_mode: ToolsMode = "json_protocol"
    reasoning_profile: dict[str, Any] | None = None


class EndpointUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_id: str | None = None
    role: Role | None = None
    ctx_window: int | None = None
    price_per_mtok_in: float | None = None
    price_per_mtok_out: float | None = None
    enabled: bool | None = None
    tools_mode: ToolsMode | None = None
    reasoning_profile: dict[str, Any] | None = None


class EndpointOut(BaseModel):
    id: str
    name: str
    base_url: str
    has_api_key: bool
    model_id: str
    role: Role
    ctx_window: int
    price_per_mtok_in: float
    price_per_mtok_out: float
    enabled: bool
    tools_mode: ToolsMode
    reasoning_profile: dict[str, Any]
    last_checked_at: str | None = None
    last_check_result: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class LevelProbeOut(BaseModel):
    level: str
    ok: bool
    reasoning_content_present: bool
    inline_tags_present: bool
    content_sample: str
    error: str | None = None


class EndpointCheckOut(BaseModel):
    models_ok: bool
    models_error: str | None
    tokenize_ok: bool
    tokenize_error: str | None
    levels: list[LevelProbeOut]
    native_tools_supported: bool
    native_tools_error: str | None = Field(default=None)
