"""User SQLAlchemy model for database-backed authentication."""

from datetime import datetime

from db.base import Base, TimestampMixin
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class User(Base, TimestampMixin):
    """Represents a user for web interface authentication.

    Users can be created from two sources:
    - "database": Credentials stored in the database (password_hash used)
    - "env": Credentials from environment variables (password_hash is placeholder)
    """

    __tablename__ = "users"

    # Auth source constants
    SOURCE_DATABASE = "database"
    SOURCE_ENV = "env"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auth_source: Mapped[str] = mapped_column(String(32), default=SOURCE_DATABASE, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_env_managed(self) -> bool:
        """Check if this user is managed via environment variables."""
        return self.auth_source == self.SOURCE_ENV

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username}, auth_source={self.auth_source})>"
