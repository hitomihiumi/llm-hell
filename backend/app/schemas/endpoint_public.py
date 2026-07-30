from pydantic import BaseModel


class ActiveEndpointOut(BaseModel):
    id: str
    name: str
    role: str
    ctx_window: int

    model_config = {"from_attributes": True}
