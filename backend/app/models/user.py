from typing import Optional

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)  # admin | user
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Nullable on purpose. Users created before the web UI existed were
    # issued an API key and nothing else, and they must keep working against
    # /v1/* - a null hash simply means "cannot log in to the web app". The
    # login route treats it exactly like a wrong password.
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Display metadata only. `username` remains the login identifier, and
    # this is deliberately NOT the account the Google connector searches -
    # that lives on the source's own config, because it is a property of the
    # connected workspace, not of whoever is logged in.
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
