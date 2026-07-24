"""Settings service for unified settings management."""

from typing import Any

import config
from camera_manager import CameraManagerSettings
from crud.backup_settings_crud import backup_settings_crud
from crud.fetch_settings_crud import fetch_settings_crud
from crud.scheduler_settings_crud import scheduler_settings_crud
from models.backup_settings_model import BackupSettings
from models.fetch_settings_model import FetchSettings
from models.scheduler_settings_model import SchedulerSettings
from sqlalchemy.ext.asyncio import AsyncSession


class SettingsCoreService:
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
        effective_username = config.UNIFI_PROTECT_USERNAME or settings.username or ""
        effective_password = config.UNIFI_PROTECT_PASSWORD or settings.password or ""
        effective_verify_ssl = (
            config.UNIFI_PROTECT_VERIFY_SSL
            if config.UNIFI_PROTECT_BASE_URL
            else settings.verify_ssl
        )

        return {
            "base_url": effective_base_url,
            "api_key": effective_api_key,
            "username": effective_username,
            "password": effective_password,
            "verify_ssl": effective_verify_ssl,
            "from_env": bool(config.UNIFI_PROTECT_BASE_URL),
            "has_api_key": bool(effective_api_key),
            "has_base_url": bool(effective_base_url),
            "has_username": bool(effective_username),
            "has_password": bool(effective_password),
            "username_from_env": bool(config.UNIFI_PROTECT_USERNAME),
            "password_from_env": bool(config.UNIFI_PROTECT_PASSWORD),
        }

    async def get_camera_manager_settings(self) -> CameraManagerSettings:
        """CameraManager settings with env-var precedence over the database."""
        s = await fetch_settings_crud.get_settings(self.db)
        return CameraManagerSettings(
            base_url=config.UNIFI_PROTECT_BASE_URL or s.base_url or "",
            api_key=config.UNIFI_PROTECT_API_KEY or s.api_key or "",
            verify_ssl=config.UNIFI_PROTECT_VERIFY_SSL
            if config.UNIFI_PROTECT_BASE_URL
            else s.verify_ssl,
            request_timeout=s.request_timeout,
            rate_limit=s.rate_limit,
            rate_limit_buffer=s.rate_limit_buffer,
            min_offset_seconds=s.min_offset_seconds,
            max_offset_seconds=s.max_offset_seconds,
            camera_refresh_interval=s.camera_refresh_interval,
        )

    async def get_protect_credentials(self) -> tuple[str, str, str, bool]:
        """(base_url, username, password, verify_ssl) with env-var precedence over the database."""
        s = await fetch_settings_crud.get_settings(self.db)
        base_url = config.UNIFI_PROTECT_BASE_URL or s.base_url or ""
        username = config.UNIFI_PROTECT_USERNAME or s.username or ""
        password = config.UNIFI_PROTECT_PASSWORD or s.password or ""
        verify_ssl = (
            config.UNIFI_PROTECT_VERIFY_SSL
            if config.UNIFI_PROTECT_BASE_URL
            else bool(s.verify_ssl)
        )
        return base_url, username, password, verify_ssl

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

    async def update_scheduler_settings(
        self, settings: dict[str, Any]
    ) -> SchedulerSettings:
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


async def get_settings_service(db: AsyncSession) -> SettingsCoreService:
    """Factory function to create SettingsCoreService instance."""
    return SettingsCoreService(db)
