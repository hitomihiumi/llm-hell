"""One user's credential for one external provider.

The row holds two very different things and keeps them apart on purpose:

  * `secret` - encrypted, never returned by any route, never logged. A GitLab
    personal access token, or a Google OAuth refresh token.
  * everything else - the account it belongs to, when it expires, which scopes
    it carries. None of that is a secret, and all of it is what the interface
    needs to say whether a source will work before the user tries it.

That split is why the API can answer "your Google token expired yesterday"
without ever being able to answer "with what token".

Column types are deliberately plain. `conftest.py` builds this schema on
SQLite, so a `JSONB` here would take the whole API-level suite down with it.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid

# The providers a user can hold a credential for. Kept as constants rather
# than an enum column because SQLite and Postgres disagree about enums and
# the set changes with the connectors, not with the schema.
PROVIDER_GOOGLE = "google"
PROVIDER_GITLAB = "gitlab"
PROVIDERS = (PROVIDER_GOOGLE, PROVIDER_GITLAB)


class UserCredential(Base, TimestampMixin):
    __tablename__ = "user_credentials"
    __table_args__ = (
        # One credential per provider per user. Re-authenticating replaces
        # rather than accumulates, so there is never a question of which of
        # three GitLab tokens a search used.
        UniqueConstraint("user_id", "provider", name="uq_user_credentials_user_provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    # Fernet ciphertext. See app/core/crypto.py for why this may live in a row
    # at all when nothing else secret does.
    secret: Mapped[str] = mapped_column(String(4096), nullable=False)

    # Which account the credential speaks for: an email for Google, a
    # username for GitLab. Shown in the interface, so the user can tell whose
    # Drive is being searched.
    account: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # When the credential stops working, when that is knowable. A Google
    # access token expires in an hour and is refreshed; a GitLab PAT may carry
    # an expiry date and simply stops. None means "no stated expiry", which is
    # not the same as "never expires".
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Whatever else is worth showing without being worth protecting: granted
    # scopes, the GitLab instance URL, the display name on the account.
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
