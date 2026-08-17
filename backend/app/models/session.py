"""Web sessions for the knowledge-base UI.

**Why a table and not a signed cookie.** A stateless signed cookie cannot be
revoked - "log out" would mean "please stop sending this", which is not the
same thing. A row can be revoked, listed, and expired server-side, and it
costs no new dependency (no PyJWT, no itsdangerous).

**Why SHA-256 and not argon2, unlike `app.core.api_keys`.** That difference
is deliberate, not an oversight. Argon2 exists to make *guessing* expensive,
which matters for values a human might have chosen or an attacker might
enumerate. A session token is 256 bits straight from `secrets.token_urlsafe`
- there is no dictionary, and no amount of hashing work changes the odds of
guessing one. What a slow KDF *does* change is that every single page load
pays ~50-100 ms of deliberate CPU burn, on a UI that fires several requests
per interaction. SHA-256 also collapses authentication into one indexed
equality lookup, instead of api_keys.py's prefix-lookup-then-verify-each-
candidate loop.

The stored value is still a hash, so a leaked database dump does not hand
over usable session tokens.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class UserSession(Base, TimestampMixin):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)

    # sha256 hex digest of the raw token. 64 chars exactly.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Refreshed at most once a minute rather than on every request - see
    # `app.core.sessions.resolve_session`.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
