"""System view service for preparing system/settings template data."""

import asyncio
import os
import platform
import shutil
import time
from datetime import datetime

import config
from db.connection import DATABASE_PATH

from services.activity_service import ActivityService
from services.camera_service import CameraService
from services.capture_stats_service import CaptureStatsService
from services.settings_service import SettingsService
from services.timelapse_browser_service import TimelapseBrowserService


class SystemViewService:
    """Prepares data for system and settings pages."""

    def __init__(
        self,
        camera_service: CameraService,
        capture_stats_service: CaptureStatsService,
        timelapse_service: TimelapseBrowserService,
        activity_service: ActivityService,
        settings_service: SettingsService,
    ):
        """Initialize with core services."""
        self.camera_service = camera_service
        self.capture_stats_service = capture_stats_service
        self.timelapse_service = timelapse_service
        self.activity_service = activity_service
        self.settings_service = settings_service

    async def get_system_context(self) -> dict:
        """Get all data needed for system page."""
        # Non-DB operations can run in parallel
        system_info, disk_info = await asyncio.gather(
            self._get_system_info(),
            self._get_disk_info(),
        )

        # DB operations must be sequential (single shared session)
        capture_stats = await self.capture_stats_service.get_stats()
        timelapse_stats = await self.timelapse_service.get_stats()
        timelapse_count = await self.timelapse_service.count()
        camera_count = await self.camera_service.count()

        # Build storage info from gathered data
        output_path = config.IMAGE_OUTPUT_PATH.parent
        storage_info = {
            "disk": disk_info,
            "image_size": capture_stats.total_file_size,
            "video_size": timelapse_stats.total_file_size,
            "output_path": str(output_path),
            "image_path": str(config.IMAGE_OUTPUT_PATH),
            "video_path": str(config.VIDEO_OUTPUT_PATH),
        }

        # Build db stats from gathered data
        db_size = await asyncio.to_thread(lambda: DATABASE_PATH.stat().st_size if DATABASE_PATH.exists() else 0)
        db_stats = {
            "capture_count": capture_stats.total_captures,
            "timelapse_count": timelapse_count,
            "camera_count": camera_count,
            "db_size": db_size,
            "db_size_mb": round(db_size / 1024 / 1024, 1),
        }

        # Fetch database settings and build status/config
        service_status = await self._get_service_status()
        config_summary = await self._get_config_summary()
        version_info = self._get_version_info()

        return {
            "system_info": system_info,
            "storage_info": storage_info,
            "db_stats": db_stats,
            "service_status": service_status,
            "config_summary": config_summary,
            "version_info": version_info,
        }

    async def get_settings_context(self) -> dict:
        """Get data for settings page."""
        config_summary = await self._get_config_summary()
        cameras = await self.camera_service.get_active()

        return {
            "config": config_summary,
            "cameras": cameras,
        }

    async def _get_system_info(self) -> dict:
        """Get system information (platform only, host metrics from external tools)."""
        return {
            "hostname": platform.node(),
            "platform": platform.system(),
            "platform_version": platform.version(),
            "python_version": platform.python_version(),
            "cpu_count": os.cpu_count() or 0,
        }

    async def _get_disk_info(self) -> dict:
        """Get disk usage info."""
        output_path = config.IMAGE_OUTPUT_PATH.parent

        def _get_disk() -> dict:
            """Read disk usage stats for the output volume."""
            if output_path.exists():
                disk_usage = shutil.disk_usage(output_path)
                percent = (disk_usage.used / disk_usage.total * 100) if disk_usage.total > 0 else 0
                return {
                    "total": disk_usage.total,
                    "used": disk_usage.used,
                    "free": disk_usage.free,
                    "percent": round(percent, 1),
                }
            return {"total": 0, "used": 0, "free": 0, "percent": 0}

        return await asyncio.to_thread(_get_disk)

    async def _get_service_status(self) -> dict:
        """Get service status information from database settings."""
        fetch_settings = await self.settings_service.get_fetch_settings()
        scheduler_settings = await self.settings_service.get_scheduler_settings()
        backup_settings = await self.settings_service.get_backup_settings()

        return {
            "web_enabled": True,  # Web always runs
            "fetch_enabled": fetch_settings.enabled,
            "timelapse_enabled": scheduler_settings.enabled,
            "backup_enabled": backup_settings.enabled,
            "capture_method": fetch_settings.default_capture_method,
            "rate_limit": fetch_settings.rate_limit,
            "fetch_intervals": fetch_settings.get_intervals(),
        }

    async def get_backup_settings_context(self) -> dict:
        """Get data for backup settings panel."""
        backup_settings = await self.settings_service.get_backup_settings()

        # Calculate interval in hours for display
        interval_hours = backup_settings.interval // 3600

        return {
            "backup_settings": backup_settings,
            "interval_hours": interval_hours,
        }

    async def _get_config_summary(self) -> dict:
        """Get configuration summary from database and config."""
        fetch_settings = await self.settings_service.get_fetch_settings()
        scheduler_settings = await self.settings_service.get_scheduler_settings()

        # Calculate effective rate limit from database settings
        effective_rate = int(fetch_settings.rate_limit * fetch_settings.rate_limit_buffer)

        return {
            # API Settings (from database)
            "api_url": fetch_settings.base_url or "",
            "verify_ssl": fetch_settings.verify_ssl,
            # Capture settings from database
            "capture_method": fetch_settings.default_capture_method,
            "snapshot_high_quality": fetch_settings.high_quality_snapshots,
            "fetch_intervals": fetch_settings.get_intervals(),
            "fetch_max_retries": fetch_settings.max_retries,
            # Rate limiting from database
            "rate_limit": fetch_settings.rate_limit,
            "rate_limit_buffer": fetch_settings.rate_limit_buffer,
            "rate_limit_buffer_percent": int(fetch_settings.rate_limit_buffer * 100),
            "effective_rate_limit": effective_rate,
            # Timelapse/FFmpeg settings from database
            "timelapse_frame_rate": scheduler_settings.frame_rate,
            "timelapse_crf": scheduler_settings.crf,
            "timelapse_preset": scheduler_settings.preset,
            # Feature toggles from database
            "web_enabled": True,
            "fetch_enabled": fetch_settings.enabled,
            "timelapse_enabled": scheduler_settings.enabled,
            # Paths (env vars - container mount points)
            "output_dir": str(config.IMAGE_OUTPUT_PATH.parent),
            "image_output_path": str(config.IMAGE_OUTPUT_PATH),
            "video_output_path": str(config.VIDEO_OUTPUT_PATH),
        }

    def _get_version_info(self) -> dict:
        """Get version information from environment variables."""
        # Get timezone - try TZ env var first, then system timezone
        tz_name = os.getenv("TZ") or time.tzname[0]
        # Get UTC offset
        utc_offset = datetime.now().astimezone().strftime("%z")
        utc_offset_formatted = f"UTC{utc_offset[:3]}:{utc_offset[3:]}" if utc_offset else ""

        return {
            "version": os.getenv("LUXUPT_VERSION", "dev"),
            "build_date": os.getenv("LUXUPT_BUILD_DATE", "unknown"),
            "python_version": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()}",
            "architecture": platform.machine(),
            "timezone": f"{tz_name} ({utc_offset_formatted})" if utc_offset_formatted else tz_name,
        }

    async def get_activity_log_context(
        self,
        *,
        activity_type: str | None = None,
        camera_id: str | None = None,
        limit: int = config.DEFAULT_PAGE_SIZE,
    ) -> dict:
        """Get activity log data."""
        activities = await self.activity_service.get_recent(
            limit=limit,
            activity_type=activity_type,
            camera_id=camera_id,
        )

        summary = await self.activity_service.get_summary(hours=24)

        return {
            "activities": activities,
            "summary": summary,
            "filters": {
                "activity_type": activity_type,
                "camera_id": camera_id,
            },
        }

    async def get_about_context(self, uptime_seconds: float = 0) -> dict:
        """Get data for about page."""
        # Get statistics and config from database
        capture_stats = await self.capture_stats_service.get_stats()
        timelapse_stats = await self.timelapse_service.get_stats()
        config_summary = await self._get_config_summary()

        # Pre-calculate display values
        total_size = capture_stats.total_file_size + timelapse_stats.total_file_size
        return {
            "version": self._get_version_info(),
            "system": {
                "uptime": uptime_seconds,
                "uptime_hours": round(uptime_seconds / 3600, 1),
            },
            "config": config_summary,
            "storage": {
                "images": {
                    "size": capture_stats.total_file_size,
                    "files": capture_stats.total_captures,
                    "size_formatted": self._format_file_size(capture_stats.total_file_size),
                },
                "videos": {
                    "size": timelapse_stats.total_file_size,
                    "files": timelapse_stats.completed_timelapses,
                    "size_formatted": self._format_file_size(timelapse_stats.total_file_size),
                },
                "total_size_gb": round(total_size / 1024 / 1024 / 1024, 1),
                "total_files": capture_stats.total_captures + timelapse_stats.completed_timelapses,
            },
        }

    def _format_file_size(self, size_bytes: float) -> str:
        """Format file size in human readable format."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"
