from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    invite_code: str
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    username: str
    role: str

    model_config = {"from_attributes": True}


class InviteCreateRequest(BaseModel):
    role: str = Field(default="user", pattern="^(admin|user)$")
    expires_in_days: int | None = None


class InviteOut(BaseModel):
    id: str
    code: str
    role: str
    used_by: str | None
    expires_at: str | None

    model_config = {"from_attributes": True}
