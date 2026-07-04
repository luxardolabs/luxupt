"""Settings service for unified settings management."""

from typing import Any

import config
from crud.backup_settings_crud import backup_settings_crud
from crud.fetch_settings_crud import fetch_settings_crud
from crud.scheduler_settings_crud import scheduler_settings_crud
from models.backup_settings import BackupSettings
from models.fetch_settings import FetchSettings
from models.scheduler_settings import SchedulerSettings
from sqlalchemy.ext.asyncio import AsyncSession


class SettingsService:
    """Service for managing application settings."""

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    # Fetch settings methods

    async def get_fetch_settings(self) -> FetchSettings:
        """Get fetch settings."""
        return await fetch_settings_crud.get_settings(self.db)

    async def get_effective_api_config(self) -> dict[str, Any]:
        """Get effective API config with env vars taking priority over database.

        Returns dict with:
            - base_url: effective base URL (env or db)
            - api_key: effective API key (env or db)
            - verify_ssl: effective SSL verification setting
            - from_env: True if settings come from environment variables
            - has_api_key: True if API key is configured
            - has_base_url: True if base URL is configured
        """
        settings = await fetch_settings_crud.get_settings(self.db)

        # Env vars take priority over database
        effective_base_url = config.UNIFI_PROTECT_BASE_URL or settings.base_url or ""
        effective_api_key = config.UNIFI_PROTECT_API_KEY or settings.api_key or ""
        effective_verify_ssl = config.UNIFI_PROTECT_VERIFY_SSL if config.UNIFI_PROTECT_BASE_URL else settings.verify_ssl

        return {
            "base_url": effective_base_url,
            "api_key": effective_api_key,
            "verify_ssl": effective_verify_ssl,
            "from_env": bool(config.UNIFI_PROTECT_BASE_URL),
            "has_api_key": bool(effective_api_key),
            "has_base_url": bool(effective_base_url),
        }

    async def update_fetch_settings(self, settings: dict[str, Any]) -> FetchSettings:
        """Update fetch settings."""
        return await fetch_settings_crud.update_settings(self.db, obj_in=settings)

    async def get_fetch_intervals(self) -> list[int]:
        """Get configured fetch intervals."""
        return await fetch_settings_crud.get_intervals(self.db)

    async def is_fetch_enabled(self) -> bool:
        """Check if fetch is enabled."""
        return await fetch_settings_crud.is_enabled(self.db)

    # Scheduler settings methods

    async def get_scheduler_settings(self) -> SchedulerSettings:
        """Get scheduler settings."""
        return await scheduler_settings_crud.get_settings(self.db)

    async def update_scheduler_settings(self, settings: dict[str, Any]) -> SchedulerSettings:
        """Update scheduler settings."""
        return await scheduler_settings_crud.update_settings(self.db, obj_in=settings)

    async def get_enabled_cameras(self) -> list[str] | None:
        """Get list of enabled cameras for scheduler, or None for all."""
        return await scheduler_settings_crud.get_enabled_cameras(self.db)

    async def get_enabled_intervals(self) -> list[int] | None:
        """Get list of enabled intervals for scheduler, or None for all."""
        return await scheduler_settings_crud.get_enabled_intervals(self.db)

    async def is_scheduler_enabled(self) -> bool:
        """Check if scheduler is enabled."""
        return await scheduler_settings_crud.is_enabled(self.db)

    async def update_scheduler_last_run(self) -> SchedulerSettings:
        """Update the scheduler last_run_at timestamp to now."""
        return await scheduler_settings_crud.update_last_run(self.db)

    # Backup settings methods

    async def get_backup_settings(self) -> BackupSettings:
        """Get backup settings."""
        return await backup_settings_crud.get_settings(self.db)

    async def update_backup_settings(self, settings: dict[str, Any]) -> BackupSettings:
        """Update backup settings."""
        return await backup_settings_crud.update_settings(self.db, obj_in=settings)

    async def is_backup_enabled(self) -> bool:
        """Check if backup is enabled (retention > 0)."""
        return await backup_settings_crud.is_enabled(self.db)


async def get_settings_service(db: AsyncSession) -> SettingsService:
    """Factory function to create SettingsService instance."""
    return SettingsService(db)
