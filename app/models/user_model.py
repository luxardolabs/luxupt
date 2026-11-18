"""User SQLAlchemy model for database-backed authentication."""

import secrets
from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.types import UtcDateTime

# Exactly the characters Python's str.strip() removes (every str.isspace() character). A login is
# stored stripped of them, and SQLite's trim(username, this) is what the unique index below holds
# to, so no two rows can differ only by surrounding whitespace.
LOGIN_WHITESPACE = (
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


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


# One login per stripped spelling: ` bob` beside `bob` is refused by the database itself.
Index(
    "ix_users_login_identity",
    func.trim(User.username, LOGIN_WHITESPACE),
    unique=True,
)
