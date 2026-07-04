"""CRUD operations for BackupSettings model."""

from models.backup_settings import BackupSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class CRUDBackupSettings:
    """CRUD operations for BackupSettings model (singleton pattern)."""

    async def get_settings(self, db: AsyncSession) -> BackupSettings:
        """Get backup settings, creating default row if it doesn't exist."""
        result = await db.execute(select(BackupSettings).where(BackupSettings.id == 1))
        settings: BackupSettings | None = result.scalar_one_or_none()

        if settings is None:
            # Create default settings - model provides all defaults
            settings = BackupSettings(id=1)
            db.add(settings)
            await db.flush()
            await db.refresh(settings)

        return settings

    async def update_settings(
        self,
        db: AsyncSession,
        *,
        obj_in: dict,
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
