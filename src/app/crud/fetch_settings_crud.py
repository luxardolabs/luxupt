"""CRUD operations for FetchSettings model."""

from models.fetch_settings import FetchSettings
from schemas.fetch_settings import FetchSettingsUpdate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class CRUDFetchSettings:
    """CRUD operations for FetchSettings model (singleton pattern)."""

    async def get_settings(self, db: AsyncSession) -> FetchSettings:
        """Get fetch settings, creating default row if it doesn't exist."""
        result = await db.execute(select(FetchSettings).where(FetchSettings.id == 1))
        settings: FetchSettings | None = result.scalar_one_or_none()

        if settings is None:
            # Create default settings - model provides all defaults
            settings = FetchSettings(
                id=1,
                intervals=[15, 30, 60, 120, 300],
            )
            db.add(settings)
            await db.flush()
            await db.refresh(settings)

        return settings

    async def update_settings(
        self,
        db: AsyncSession,
        *,
        obj_in: FetchSettingsUpdate | dict,
    ) -> FetchSettings:
        """Update fetch settings."""
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

    async def get_intervals(self, db: AsyncSession) -> list[int]:
        """Get configured intervals."""
        settings = await self.get_settings(db)
        return settings.get_intervals()

    async def is_enabled(self, db: AsyncSession) -> bool:
        """Check if fetch is enabled."""
        settings = await self.get_settings(db)
        return settings.enabled


fetch_settings_crud = CRUDFetchSettings()
