"""Pydantic schemas for BackupSettings operations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BackupSettingsRead(BaseModel):
    """Schema for reading BackupSettings."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    retention: int
    interval: int
    backup_dir: str
    created_at: datetime
    updated_at: datetime

    @property
    def enabled(self) -> bool:
        """Backups are enabled when any are retained (retention > 0)."""
        return self.retention > 0
