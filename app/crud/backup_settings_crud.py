"""CRUD operations for BackupSettings model."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import with_column_defaults
from app.models.backup_settings_model import BackupSettings


class CRUDBackupSettings:
    """CRUD operations for BackupSettings model (singleton pattern)."""

    async def get_settings(self, db: AsyncSession) -> BackupSettings:
        """Get backup settings, creating default row if it doesn't exist."""
        result = await db.execute(select(BackupSettings).where(BackupSettings.id == 1))
        settings: BackupSettings | None = result.scalar_one_or_none()

        if settings is None:
            # NOT persisted here. A GET page render reaches this read, and a GET that writes is
            # CSRF-reachable under SameSite=Lax (fw.state_changing_get). The real row is seeded
            # once at startup by init_db(); this returns transient defaults so a read before
            # seeding still renders. Persisting is the writer's job, never the reader's.
            settings = with_column_defaults(BackupSettings(id=1))

        return settings

    async def update_settings(
        self,
        db: AsyncSession,
        *,
        obj_in: dict[str, Any],
    ) -> BackupSettings:
        """Update backup settings."""
        settings = await self.get_settings(db)

        for field, value in obj_in.items():
            if hasattr(settings, field):
                setattr(settings, field, value)

        db.add(settings)
        await db.flush()
        await db.refresh(settings)
        return settings

    async def is_enabled(self, db: AsyncSession) -> bool:
        """Check if backup is enabled (retention > 0)."""
        settings = await self.get_settings(db)
        return settings.enabled


backup_settings_crud = CRUDBackupSettings()
