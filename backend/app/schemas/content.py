from pydantic import BaseModel


class ContentOut(BaseModel):
    """The readable body behind a hit.

    `language` is a hint for the viewer's monospace/prose decision, not a
    promise: a source that does not know answers None and the viewer falls
    back to reading it as text.
    """

    hit_id: str
    title: str
    text: str
    language: str | None = None
    truncated: bool = False
