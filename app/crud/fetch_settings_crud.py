"""CRUD operations for FetchSettings model."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import with_column_defaults
from app.models.fetch_settings_model import FetchSettings
from app.schemas.fetch_settings_schema import FetchSettingsUpdate


class CRUDFetchSettings:
    """CRUD operations for FetchSettings model (singleton pattern)."""

    async def get_settings(self, db: AsyncSession) -> FetchSettings:
        """Get fetch settings, creating default row if it doesn't exist."""
        result = await db.execute(select(FetchSettings).where(FetchSettings.id == 1))
        settings: FetchSettings | None = result.scalar_one_or_none()

        if settings is None:
            # NOT persisted here. A GET page render reaches this read, and a GET that writes is
            # CSRF-reachable under SameSite=Lax (fw.state_changing_get). The real row is seeded
            # once at startup by init_db(); this returns transient defaults so a read before
            # seeding still renders. Persisting is the writer's job, never the reader's.
            settings = with_column_defaults(
                FetchSettings(id=1, intervals=[15, 30, 60, 120, 300])
            )

        return settings

    async def update_settings(
        self,
        db: AsyncSession,
        *,
        obj_in: FetchSettingsUpdate | dict[str, Any],
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
