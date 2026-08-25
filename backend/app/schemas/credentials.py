"""What the credential routes accept and return.

`CredentialStatusOut` has no field for a secret, and that is the design
rather than an omission: the response model is what makes it impossible to
leak one by adding a line to a handler.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CredentialStatusOut(BaseModel):
    """One provider, as the interface may see it."""

    provider: Literal["google", "gitlab"]
    connected: bool
    # The account the credential speaks for - an email, or a GitLab username.
    # Shown so a user can tell whose Drive a search is reading.
    account: str | None = None
    expires_at: datetime | None = None
    expired: bool = False
    last_verified_at: datetime | None = None
    # Scopes, instance URL, display name. Non-secret by construction.
    detail: dict[str, Any] = Field(default_factory=dict)
    # False when CREDENTIALS_ENCRYPTION_KEY is unset, which is not a fault:
    # the deployment simply uses its own tokens for everyone. The interface
    # needs it to explain why connecting is unavailable.
    storage_available: bool = True


class GitLabTokenIn(BaseModel):
    # Long enough for any GitLab token format, bounded so a paste accident
    # cannot become a 10 MB request body.
    token: str = Field(min_length=8, max_length=512)
    # Optional per-user instance. A contractor on a different GitLab is a
    # real case, and the alternative is one shared URL for everybody.
    api_url: str | None = Field(default=None, max_length=512)


class GoogleAuthStartOut(BaseModel):
    email: str
    # Whether the Workspace server already holds working credentials for the
    # address. When false, `message` says how to add them - the server cannot
    # run consent itself.
    authenticated: bool = False
    # Reserved. The Workspace server emits no consent URL, because it opens a
    # browser rather than printing one; kept so a future server that does can
    # be used without changing the contract.
    url: str | None = None
    message: str = ""
