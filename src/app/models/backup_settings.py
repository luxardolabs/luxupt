"""BackupSettings SQLAlchemy model for database backup configuration."""

from db.base import Base, TimestampMixin
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class BackupSettings(Base, TimestampMixin):
    """Singleton model for database backup settings."""

    __tablename__ = "backup_settings"

    # Always id=1 (singleton pattern)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Number of backups to retain (0 = disabled)
    retention: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Backup interval in seconds (default 1 hour)
    interval: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)

    # Backup destination directory (relative to OUTPUT_DIR, default "backups")
    backup_dir: Mapped[str] = mapped_column(String(256), default="backups", nullable=False)

    def __repr__(self) -> str:
        return f"<BackupSettings(retention={self.retention}, interval={self.interval})>"

    @property
    def enabled(self) -> bool:
        """Check if backups are enabled (retention > 0)."""
        return self.retention > 0
