"""Cameras view service for preparing camera template data."""

import time
from datetime import datetime

from camera_manager import CameraManager
from logging_config import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from services.camera_service import CameraService
from services.capture_service import CaptureService
from services.capture_stats_service import CaptureStatsService
from services.settings_service import SettingsService

logger = get_logger(__name__)

# Bucket sizes for capture statistics time series (period -> seconds per bucket)
CAPTURE_STATS_BUCKET_SIZES = {
    "1h": 60,  # 1 minute buckets for 1 hour
    "6h": 300,  # 5 minute buckets for 6 hours
    "24h": 900,  # 15 minute buckets for 24 hours
    "7d": 3600,  # 1 hour buckets for 7 days
    "30d": 86400,  # 1 day buckets for 30 days
}
CAPTURE_STATS_DEFAULT_BUCKET = 900  # 15 minutes


class CamerasViewService:
    """Prepares data for camera pages."""

    def __init__(
        self,
        db: AsyncSession,
        camera_service: CameraService,
        capture_service: CaptureService,
        capture_stats_service: CaptureStatsService,
        settings_service: SettingsService,
    ):
        """Initialize with services."""
        self.db = db
        self.camera_service = camera_service
        self.capture_service = capture_service
        self.capture_stats_service = capture_stats_service
        self.settings_service = settings_service

    async def get_camera_card_context(self, safe_name: str) -> dict:
        """Get data for a single camera card."""
        camera = await self.camera_service.get_by_safe_name(safe_name)
        if not camera:
            return {"camera": None, "latest_capture": None, "has_thumbnail": False}

        latest_capture = await self.capture_service.get_latest_by_camera(camera.camera_id)

        return {
            "camera": camera,
            "latest_capture": latest_capture,
            "has_thumbnail": latest_capture is not None,
        }

    async def get_fetch_settings_context(self) -> dict:
        """Get data for fetch settings panel."""
        settings = await self.settings_service.get_fetch_settings()

        # Get effective API config from core service (handles env var priority)
        api_config = await self.settings_service.get_effective_api_config()

        # Add display-only fields for template
        api_config["base_url_display"] = self._mask_url(api_config["base_url"]) if api_config["base_url"] else None

        # Highlight API section if not configured
        needs_api = not api_config["has_api_key"] and not api_config["has_base_url"]

        # Get disabled cameras so users can re-enable them
        inactive_cameras = await self.camera_service.get_inactive()

        return {
            "settings": settings,
            "api_config": api_config,
            "needs_api": needs_api,
            "inactive_cameras": inactive_cameras,
        }

    def _mask_url(self, url: str) -> str:
        """Extract domain from URL for safe display."""
        try:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}/..."
        except Exception:
            return "configured"

    async def get_camera_settings_context(self, camera_id: str) -> dict:
        """Get data for camera settings panel."""
        camera = await self.camera_service.get_by_id(camera_id)
        if not camera:
            return {"camera": None, "intervals": []}

        fetch_settings = await self.settings_service.get_fetch_settings()
        intervals = fetch_settings.get_intervals()

        return {
            "camera": camera,
            "intervals": intervals,
        }

    async def get_capture_stats_context(self) -> dict:
        """Get data for capture statistics panel."""
        cameras = await self.camera_service.get_active()
        intervals = await self.capture_service.get_available_intervals()

        return {
            "cameras": cameras,
            "intervals": intervals,
        }

    async def get_capture_stats_charts_context(
        self,
        *,
        camera: str | None = None,
        interval: int | None = None,
        period: str = "24h",
        offset: int = 0,
    ) -> dict:
        """Get data for capture statistics charts."""
        # Period configuration: seconds and max history windows
        period_config = {
            "1h": {"seconds": 60 * 60, "max_back": 48},
            "6h": {"seconds": 6 * 60 * 60, "max_back": 28},
            "24h": {"seconds": 24 * 60 * 60, "max_back": 30},
            "7d": {"seconds": 7 * 24 * 60 * 60, "max_back": 8},
        }
        cfg = period_config.get(period, period_config["24h"])
        seconds = cfg["seconds"]
        max_back = cfg["max_back"]

        # Clamp offset
        offset = max(offset, -max_back)
        offset = min(offset, 0)

        # Calculate time range
        now = int(time.time())
        end_timestamp = now + (offset * seconds) + seconds
        since_timestamp = end_timestamp - seconds

        if offset == 0:
            end_timestamp = now
            since_timestamp = now - seconds

        # Navigation state
        can_navigate_back = offset > -max_back
        can_navigate_forward = offset < 0

        # Datetime range for template formatting
        start_dt = datetime.fromtimestamp(since_timestamp)
        end_dt = datetime.fromtimestamp(end_timestamp)

        # Get bucket size for time series
        bucket_seconds = CAPTURE_STATS_BUCKET_SIZES.get(period, CAPTURE_STATS_DEFAULT_BUCKET)

        # Get time series data
        time_series = await self.capture_stats_service.get_time_series(
            camera=camera,
            interval=interval,
            since_timestamp=since_timestamp,
            until_timestamp=end_timestamp,
            bucket_seconds=bucket_seconds,
        )

        # Prepare chart data
        duration_data = []
        size_data = []
        for item in time_series:
            duration_data.append(
                {
                    "timestamp": item["timestamp"],
                    "value": item["avg_duration_ms"],
                    "count": item["total_count"],
                    "cameras": {k: v["duration_ms"] for k, v in item["cameras"].items()},
                }
            )
            size_data.append(
                {
                    "timestamp": item["timestamp"],
                    "value": item["avg_file_size"],
                    "count": item["total_count"],
                    "cameras": {k: v["file_size"] for k, v in item["cameras"].items()},
                }
            )

        # Get success/failure stats and timeseries
        sf_stats = await self.capture_stats_service.get_success_failure(
            camera=camera,
            interval=interval,
            since_timestamp=since_timestamp,
            until_timestamp=end_timestamp,
        )
        sf_timeseries = await self.capture_stats_service.get_success_failure_timeseries(
            camera=camera,
            interval=interval,
            since_timestamp=since_timestamp,
            until_timestamp=end_timestamp,
            bucket_seconds=bucket_seconds,
        )

        # Prepare success/failure chart data
        success_data = [[item["timestamp"], item["success"]] for item in sf_timeseries]
        failure_data = [[item["timestamp"], item["failed"]] for item in sf_timeseries]

        # Get camera breakdown
        camera_breakdown = []
        if not camera:
            camera_breakdown = await self.capture_stats_service.get_camera_breakdown(
                interval=interval,
                since_timestamp=since_timestamp,
                until_timestamp=end_timestamp,
            )

        # Get filtered average stats
        avg_stats = await self.capture_stats_service.get_averages(
            camera=camera,
            interval=interval,
            since_timestamp=since_timestamp,
            until_timestamp=end_timestamp,
        )

        # Pre-calculate percentage for camera breakdown bars
        max_count = camera_breakdown[0]["count"] if camera_breakdown else 1
        for item in camera_breakdown:
            item["percentage"] = round(item["count"] / max_count * 100) if max_count > 0 else 0

        return {
            "duration_data": duration_data,
            "size_data": size_data,
            "success_data": success_data,
            "failure_data": failure_data,
            "sf_stats": sf_stats,
            "camera_breakdown": camera_breakdown,
            "avg_duration_ms": int(round(avg_stats["avg_duration_ms"])),
            "avg_file_size_kb": int(round(avg_stats["avg_file_size"] / 1024)),
            "period": period,
            "offset": offset,
            "camera": camera,
            "interval": interval,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "can_navigate_back": can_navigate_back,
            "can_navigate_forward": can_navigate_forward,
        }

    async def get_camera_panel_context(self, safe_name: str) -> dict:
        """Get data for camera detail panel."""
        camera = await self.camera_service.get_by_safe_name(safe_name)
        if not camera:
            return {"camera": None, "latest_capture": None, "stats": {}}

        latest_capture = await self.capture_service.get_latest_by_camera(camera.camera_id)
        fetch_settings = await self.settings_service.get_fetch_settings()
        global_intervals = fetch_settings.get_intervals()
        stats = await self.camera_service.get_stats(camera.camera_id, global_intervals=global_intervals)

        return {
            "camera": camera,
            "latest_capture": latest_capture,
            "stats": stats,
        }

    async def get_camera_detail_context(self, safe_name: str) -> dict:
        """Get data for camera detail page."""
        camera = await self.camera_service.get_by_safe_name(safe_name)
        if not camera:
            return {"camera": None, "latest_capture": None, "stats": {}}

        latest_capture = await self.capture_service.get_latest_by_camera(camera.camera_id)
        fetch_settings = await self.settings_service.get_fetch_settings()
        global_intervals = fetch_settings.get_intervals()
        stats = await self.camera_service.get_stats(camera.camera_id, global_intervals=global_intervals)

        return {
            "camera": camera,
            "latest_capture": latest_capture,
            "stats": stats,
        }

    async def update_fetch_settings(self, update_data: dict) -> tuple[bool, str, int | None]:
        """Update fetch settings and sync cameras if API configured.

        Returns (success, message, cameras_synced).
        cameras_synced is None if no API settings, -1 on connection error, or count on success.
        """
        from fetch_service import FetchService

        try:
            # Save settings and commit so FetchService can read them
            await self.settings_service.update_fetch_settings(update_data)
            await self.db.commit()

            # Check if we have API settings to test (core service handles env var priority)
            api_config = await self.settings_service.get_effective_api_config()
            if not api_config["has_base_url"] or not api_config["has_api_key"]:
                return True, "Settings saved.", None

            # Sync cameras using FetchService
            fetch_service = FetchService()
            synced = await fetch_service.sync_cameras()

            if synced >= 0:
                return True, f"Settings saved. Connected! Found {synced} camera(s).", synced
            else:
                return True, "Settings saved, but connection failed.", synced

        except Exception as e:
            logger.error("Error updating fetch settings", extra={"error": str(e)})
            return False, str(e), None

    async def update_camera_settings(self, camera_id: str, update_data: dict) -> tuple[bool, str]:
        """Update camera settings."""
        try:
            camera = await self.camera_service.get_by_id(camera_id)
            if not camera:
                return False, "Camera not found"

            await self.camera_service.update_settings(camera_id, update_data)
            return True, f"Settings saved for {camera.name}"
        except Exception as e:
            logger.error("Error updating camera settings", extra={"camera_id": camera_id, "error": str(e)})
            return False, str(e)

    async def detect_camera_capabilities(self, camera_id: str, camera_manager: CameraManager) -> dict | None:
        """Run capability detection for a camera."""
        from camera_manager import Camera as ApiCamera

        camera = await self.camera_service.get_by_id(camera_id)
        if not camera:
            return None

        if not camera.is_connected:
            return None

        # Create API camera object for detection
        api_camera = ApiCamera(
            id=camera.camera_id,
            name=camera.name,
            state=camera.state,
            mac=camera.mac or "",
            is_connected=camera.is_connected,
            is_recording=camera.is_recording,
            model_key=camera.model_key,
            video_mode=camera.video_mode,
            hdr_type=camera.hdr_type,
            supports_full_hd_snapshot=camera.supports_full_hd_snapshot,
            has_hdr=camera.has_hdr,
            has_mic=camera.has_mic,
            has_speaker=camera.has_speaker,
            smart_detect_types=camera.smart_detect_types,
        )

        # Run capability detection
        capabilities = await camera_manager.detect_camera_capabilities(api_camera)

        # Update camera with detection results
        await self.camera_service.update_capability_detection(
            camera_id,
            api_max_resolution=capabilities.get("api_max_resolution"),
            rtsp_max_resolution=capabilities.get("rtsp_max_resolution"),
            recommended_method=capabilities.get("recommended_method"),
        )

        return capabilities

    async def delete_camera(self, camera_id: str) -> tuple[bool, str]:
        """Delete a camera.

        Returns (success, message).
        """
        try:
            camera = await self.camera_service.get_by_id(camera_id)
            if not camera:
                return False, "Camera not found"

            camera_name = camera.name
            deleted = await self.camera_service.delete(camera_id)

            if deleted:
                logger.info("Deleted camera", extra={"camera_id": camera_id, "camera_name": camera_name})
                return True, f"Deleted camera: {camera_name}"
            else:
                return False, "Failed to delete camera"
        except Exception as e:
            logger.error("Error deleting camera", extra={"camera_id": camera_id, "error": str(e)})
            return False, str(e)
