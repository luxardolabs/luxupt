"""User SQLAlchemy model for database-backed authentication."""

import secrets
from datetime import datetime

from sqlalchemy import Boolean, Integer, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.types import UtcDateTime


def new_session_version() -> int:
    """A value no token carries yet. Random, so no sequence of edits can walk a row onto a token."""
    return secrets.randbits(62)


class User(Base, TimestampMixin):
    """A user of the web interface. The database is the only credential store.

    The first admin comes from INITIAL_ADMIN_* or /setup, and recovery is
    ``python -m app.cli reset-password <username>`` (FLEET-ADMIN-BOOTSTRAP-STANDARD).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Set on a seeded, reset or admin-assigned password: the next sign-in goes to the change form.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    # In every session token; replaced when the password or username changes, so older sessions
    # stop working. A token names its user by username, and every value is random, so a token issued
    # to a deleted or renamed user does not fit whoever holds that name next.
    session_version: Mapped[int] = mapped_column(
        Integer, default=new_session_version, server_default="0", nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username})>"
