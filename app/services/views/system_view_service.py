"""System view service for preparing system/settings template data."""

import asyncio
import os
import platform
import shutil
import time
from datetime import datetime, timedelta
from typing import Any

from app import config
from app.db.connection import DATABASE_PATH
from app.models.enum_model import ACTIVITY_TYPE_LABELS, ActivityType
from app.schemas.pagination_schema import build_pagination
from app.services.core.activity_core_service import ActivityCoreService
from app.services.core.camera_core_service import CameraCoreService
from app.services.core.capture_stats_core_service import CaptureStatsCoreService
from app.services.core.settings_core_service import SettingsCoreService
from app.services.core.timelapse_browser_core_service import TimelapseBrowserCoreService
from app.services.views._camera_options import (
    build_camera_card_urls,
    build_camera_options,
    build_job_card_urls,
)


class SystemViewService:
    """Prepares data for system and settings pages."""

    def __init__(
        self,
        camera_service: CameraCoreService,
        capture_stats_service: CaptureStatsCoreService,
        timelapse_service: TimelapseBrowserCoreService,
        activity_service: ActivityCoreService,
        settings_service: SettingsCoreService,
    ):
        """Initialize with core services."""
        self.camera_service = camera_service
        self.capture_stats_service = capture_stats_service
        self.timelapse_service = timelapse_service
        self.activity_service = activity_service
        self.settings_service = settings_service

    async def get_system_context(self) -> dict[str, Any]:
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
        db_size = await asyncio.to_thread(
            lambda: DATABASE_PATH.stat().st_size if DATABASE_PATH.exists() else 0
        )
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

    async def _get_system_info(self) -> dict[str, Any]:
        """Get system information (platform only, host metrics from external tools)."""
        return {
            "hostname": platform.node(),
            "platform": platform.system(),
            "platform_version": platform.version(),
            "python_version": platform.python_version(),
            "cpu_count": os.cpu_count() or 0,
        }

    async def _get_disk_info(self) -> dict[str, Any]:
        """Get disk usage info."""
        output_path = config.IMAGE_OUTPUT_PATH.parent

        def _get_disk() -> dict[str, Any]:
            """Read disk usage stats for the output volume."""
            if output_path.exists():
                disk_usage = shutil.disk_usage(output_path)
                percent = (
                    (disk_usage.used / disk_usage.total * 100)
                    if disk_usage.total > 0
                    else 0
                )
                return {
                    "total": disk_usage.total,
                    "used": disk_usage.used,
                    "free": disk_usage.free,
                    "percent": round(percent, 1),
                }
            return {"total": 0, "used": 0, "free": 0, "percent": 0}

        return await asyncio.to_thread(_get_disk)

    async def _get_service_status(self) -> dict[str, Any]:
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

    async def save_backup_settings(
        self, *, retention: int, interval_seconds: int, backup_dir: str
    ) -> None:
        """Persist the backup settings the form submitted.

        The router calls the VIEW; the view calls CORE. Reaching
        `view_service.settings_service` from the router skipped this seam
        (fw.no_layer_reach_through) — the import ban cannot see it because core is reached
        through a held reference rather than imported.
        """
        await self.settings_service.update_backup_settings(
            {
                "retention": retention,
                "interval": interval_seconds,
                "backup_dir": backup_dir.strip() or "backups",
            }
        )

    async def get_backup_settings_context(self) -> dict[str, Any]:
        """Get data for backup settings panel."""
        backup_settings = await self.settings_service.get_backup_settings()

        # Calculate interval in hours for display
        interval_hours = backup_settings.interval // 3600

        return {
            "backup_settings": backup_settings,
            "interval_hours": interval_hours,
        }

    async def _get_config_summary(self) -> dict[str, Any]:
        """Get configuration summary from database and config."""
        fetch_settings = await self.settings_service.get_fetch_settings()
        scheduler_settings = await self.settings_service.get_scheduler_settings()

        # Calculate effective rate limit from database settings
        effective_rate = int(
            fetch_settings.rate_limit * fetch_settings.rate_limit_buffer
        )

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
            # Rendered by pages/about.html. Absent until now, so under Jinja's default
            # Undefined the About page showed BLANK values instead of failing
            # (fw.jinja_strict_undefined).
            "concurrent_limit": scheduler_settings.concurrent_jobs,
            "min_offset_seconds": fetch_settings.min_offset_seconds,
            "max_offset_seconds": fetch_settings.max_offset_seconds,
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

    def _get_version_info(self) -> dict[str, Any]:
        """Get version information from environment variables."""
        # Get timezone - try TZ env var first, then system timezone
        tz_name = os.getenv("TZ") or time.tzname[0]
        # The server's own UTC offset, computed from the offset itself rather than by
        # slicing a strftime("%z") string (fw.strftime_is_display_only; the slicing was also
        # fragile — it assumed a fixed-width ±HHMM and silently produced junk otherwise).
        # This reports a configuration FACT about the host, not a timestamp for a viewer.
        offset = datetime.now().astimezone().utcoffset() or timedelta(0)
        total_minutes = int(offset.total_seconds()) // 60
        sign = "+" if total_minutes >= 0 else "-"
        hours, minutes = divmod(abs(total_minutes), 60)
        utc_offset_formatted = f"UTC{sign}{hours:02d}:{minutes:02d}"

        return {
            "version": os.getenv("BUILD_VERSION", "dev"),
            "build_date": os.getenv("BUILD_TIMESTAMP", "unknown"),
            "python_version": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()}",
            "architecture": platform.machine(),
            "timezone": f"{tz_name} ({utc_offset_formatted})"
            if utc_offset_formatted
            else tz_name,
        }

    # "Problems" = failures + errors (the default view of the log)
    PROBLEM_TYPES = ["capture_failed", "timelapse_failed", "error"]

    # Time-range options: value -> (hours or None for "all", label)
    PERIODS: dict[str, tuple[int | None, str]] = {
        "24h": (24, "Last 24 Hours"),
        "7d": (24 * 7, "Last 7 Days"),
        "30d": (24 * 30, "Last 30 Days"),
        "all": (None, "All Time"),
    }

    async def get_activity_log_context(
        self,
        *,
        show: str = "problems",
        period: str = "7d",
        camera_id: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, Any]:
        """Get activity log data — paginated and grouped by day for the feed.

        `show`: 'problems' (default: failures + errors), 'all', or a specific
        activity_type value. `period`: 24h / 7d / 30d / all — bounds the feed and
        the summary to the same window.
        """
        if show == "all":
            activity_types: list[str] | None = None
        elif show in ("", "problems"):
            activity_types = self.PROBLEM_TYPES
        else:
            activity_types = [show]

        hours, period_label = self.PERIODS.get(period, self.PERIODS["7d"])
        since = datetime.now() - timedelta(hours=hours) if hours is not None else None

        total = await self.activity_service.count(
            activity_types=activity_types,
            camera_id=camera_id,
            since=since,
        )
        activities = await self.activity_service.get_recent(
            limit=per_page,
            offset=(page - 1) * per_page,
            activity_types=activity_types,
            camera_id=camera_id,
            since=since,
        )

        # Group this page's rows by calendar day (list preserves newest-first order).
        # The day's LABEL is the template's job (the day_label filter) -- grouping is not.
        activity_groups: list[dict[str, Any]] = []
        for a in activities:
            day = a.timestamp.date()
            if not activity_groups or activity_groups[-1]["date"] != day:
                activity_groups.append({"date": day, "items": []})
            activity_groups[-1]["items"].append(a)

        # Summary counts the same window as the feed ("all" -> effectively unbounded)
        summary = await self.activity_service.get_summary(
            hours=hours if hours is not None else 24 * 3660
        )
        cameras = await self.camera_service.get_active()

        # Filter options derived from the enum (single source of truth — can't drift).
        # web_request is an internal request log, not a user-facing event.
        activity_type_options = [
            {"value": t.value, "label": ACTIVITY_TYPE_LABELS[t]}
            for t in ActivityType
            if t is not ActivityType.WEB_REQUEST
        ]
        # All time ranges in natural order (the PERIODS dict is ordered 24h→7d→30d→all);
        # the period select has no placeholder (it always has a value).
        period_options = [
            {"value": key, "label": label} for key, (_, label) in self.PERIODS.items()
        ]

        return {
            "activity_groups": activity_groups,
            "activity_count": len(activities),
            "summary": summary,
            "summary_label": period_label,
            "cameras": cameras,
            "camera_options": build_camera_options(cameras),
            "activity_type_options": activity_type_options,
            "period_options": period_options,
            "filters": {
                "show": show,
                "period": period,
                "camera_id": camera_id,
            },
            "pagination": build_pagination(page=page, per_page=per_page, total=total),
        }

    async def get_about_context(self, uptime_seconds: float = 0) -> dict[str, Any]:
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
                    "size_formatted": self._format_file_size(
                        capture_stats.total_file_size
                    ),
                },
                "videos": {
                    "size": timelapse_stats.total_file_size,
                    "files": timelapse_stats.completed_timelapses,
                    "size_formatted": self._format_file_size(
                        timelapse_stats.total_file_size
                    ),
                },
                "total_size_gb": round(total_size / 1024 / 1024 / 1024, 1),
                "total_files": capture_stats.total_captures
                + timelapse_stats.completed_timelapses,
            },
        }

    def _format_file_size(self, size_bytes: float) -> str:
        """Format file size in human readable format."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

    def get_components_context(self) -> dict[str, object]:
        """Sample domain objects for the dev-only component showcase.

        The showcase must *render* every data-bound macro (camera_card, the job
        cards, timelapse_card, image_card, pagination_controls), not just import
        it — that is the point of a component library. These macros take live
        domain objects + custom filters, so the fixtures are built here (real
        datetimes, real Pagination DTO) and passed into the template. Never
        hand-roll pagination state in the template (fw.template_pagination)."""
        now = datetime.now()
        today = now.date().isoformat()

        return {
            "demo_camera_urls": build_camera_card_urls("demo-cam-1"),
            "demo_timelapse_urls": {
                "demo-tl-1": {
                    "lightbox": "/timelapses/demo-tl-1/lightbox",
                    "video": "/timelapses/demo-tl-1/video",
                    "delete": "/timelapses/demo-tl-1",
                    "target": "#timelapse-demo-tl-1",
                }
            },
            "demo_job_urls": build_job_card_urls(
                ["demo-job-run", "demo-job-pend", "demo-job-done"]
            ),
            "demo_camera": {
                "safe_name": "front_door",
                "camera_id": "demo-cam-1",
                "name": "Front Door",
                "is_connected": True,
            },
            "demo_latest_capture": {
                "timestamp": int(now.timestamp()),
                "interval": 60,
                "capture_date": today,
                "capture_datetime": now - timedelta(minutes=3),
            },
            "demo_camera_stats": {
                "total_captures": 18432,
                "success_rate": 98.6,
                "timelapse_count": 42,
                "capture_days": 31,
                "captures_today": 1287,
                "today_summary": {
                    "success": 1287,
                    "failed": 3,
                    "expected": 1440,
                    "rate": 89,
                },
                "interval_stats": {
                    60: {"success": 1287, "failed": 3, "expected": 1440, "rate": 89},
                    300: {"success": 288, "failed": 0, "expected": 288, "rate": 100},
                },
            },
            "demo_capture": {
                "interval": 60,
                "camera_safe_name": "front_door",
                "timestamp": int(now.timestamp()),
                "capture_date": today,
                "capture_datetime": now - timedelta(minutes=3),
            },
            "demo_running_job": {
                "job_id": "demo-job-run",
                "camera_safe_name": "front_door",
                "interval": 60,
                "target_date": today,
                "current_image": None,
                "progress": 63,
                "message": "Encoding frames…",
            },
            "demo_pending_job": {
                "job_id": "demo-job-pend",
                "camera_safe_name": "back_yard",
                "interval": 300,
                "target_date": today,
                "image_count": 288,
            },
            "demo_completed_job": {
                "job_id": "demo-job-done",
                "status": "completed",
                "camera_safe_name": "front_door",
                "target_date": (now - timedelta(days=1)).date().isoformat(),
                "interval": 60,
                "total_frames": 1440,
                "started_at": now - timedelta(minutes=8),
                "completed_at": now - timedelta(minutes=6),
                "created_at": now - timedelta(minutes=9),
                "message": None,
                "error": None,
            },
            "demo_timelapse": {
                "id": "demo-tl-1",
                "status": "completed",
                "file_path": "/demo.mp4",
                "camera_safe_name": "front_door",
                "timelapse_date": now.date(),
                "end_date": None,
                "interval": 60,
                "file_name": "front_door.mp4",
                "frame_count": 1440,
                "duration_seconds": 48.0,
                "resolution": "1920x1080",
                "file_size": 734003200,
            },
            "demo_pagination": build_pagination(page=2, per_page=20, total=97),
        }
