"""CRUD operations for SchedulerSettings model."""

from datetime import datetime
from typing import cast

from models.scheduler_settings import SchedulerSettings
from schemas.scheduler_settings import SchedulerSettingsUpdate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class CRUDSchedulerSettings:
    """CRUD operations for SchedulerSettings model (singleton pattern)."""

    async def get_settings(self, db: AsyncSession) -> SchedulerSettings:
        """Get scheduler settings, creating default row if it doesn't exist."""
        result = await db.execute(select(SchedulerSettings).where(SchedulerSettings.id == 1))
        settings: SchedulerSettings | None = result.scalar_one_or_none()

        if settings is None:
            # Create default settings
            settings = SchedulerSettings(id=1)
            db.add(settings)
            await db.flush()
            await db.refresh(settings)

        return settings

    async def update_settings(
        self,
        db: AsyncSession,
        *,
        obj_in: SchedulerSettingsUpdate | dict,
    ) -> SchedulerSettings:
        """Update scheduler settings."""
        settings = await self.get_settings(db)

        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.model_dump(exclude_unset=True)

        for field, value in update_data.items():
            if hasattr(settings, field):
                setattr(settings, field, value)

        db.add(settings)
        await db.flush()
        await db.refresh(settings)
        return settings

    async def update_last_run(self, db: AsyncSession) -> SchedulerSettings:
        """Update the last_run_at timestamp to now."""
        settings = await self.get_settings(db)
        settings.last_run_at = datetime.now()
        db.add(settings)
        await db.flush()
        await db.refresh(settings)
        return settings

    async def get_enabled_cameras(self, db: AsyncSession) -> list[str] | None:
        """Get list of enabled cameras, or None for all."""
        settings = await self.get_settings(db)
        return cast(list[str] | None, settings.enabled_cameras)

    async def get_enabled_intervals(self, db: AsyncSession) -> list[int] | None:
        """Get list of enabled intervals, or None for all."""
        settings = await self.get_settings(db)
        return cast(list[int] | None, settings.enabled_intervals)

    async def is_enabled(self, db: AsyncSession) -> bool:
        """Check if scheduler is enabled."""
        settings = await self.get_settings(db)
        return settings.enabled


scheduler_settings_crud = CRUDSchedulerSettings()
