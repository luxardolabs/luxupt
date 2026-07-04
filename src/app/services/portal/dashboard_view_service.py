"""Dashboard view service for preparing dashboard template data."""

from services.activity_service import ActivityService
from services.camera_service import CameraService
from services.capture_service import CaptureService
from services.capture_stats_service import CaptureStatsService
from services.job_service import JobService
from services.settings_service import SettingsService
from services.timelapse_browser_service import TimelapseBrowserService


class DashboardViewService:
    """Prepares data for dashboard page templates."""

    def __init__(
        self,
        camera_service: CameraService,
        capture_service: CaptureService,
        capture_stats_service: CaptureStatsService,
        timelapse_service: TimelapseBrowserService,
        job_service: JobService,
        activity_service: ActivityService,
        settings_service: SettingsService,
    ):
        """Initialize with core services."""
        self.camera_service = camera_service
        self.capture_service = capture_service
        self.capture_stats_service = capture_stats_service
        self.timelapse_service = timelapse_service
        self.job_service = job_service
        self.activity_service = activity_service
        self.settings_service = settings_service

    async def get_dashboard_context(self) -> dict:
        """Get all data needed for the main dashboard page."""
        # Get camera data
        cameras = await self.camera_service.get_active()
        connected_cameras = [c for c in cameras if c.is_connected]

        # Get capture statistics
        capture_stats = await self.capture_stats_service.get_stats()

        # Get latest captures for thumbnails
        latest_captures = await self.capture_service.get_latest_captures_all()

        # Get timelapse statistics
        timelapse_stats = await self.timelapse_service.get_stats()

        # Get job status
        active_jobs = await self.job_service.get_active()
        recent_completed_jobs = await self.job_service.get_completed(limit=5)

        # Get recent activity
        recent_activities = await self.activity_service.get_recent(limit=20)
        activity_summary = await self.activity_service.get_summary(hours=24)

        return {
            "cameras": cameras,
            "connected_cameras": connected_cameras,
            "camera_count": len(cameras),
            "connected_count": len(connected_cameras),
            "latest_captures": latest_captures,
            "capture_stats": capture_stats,
            "timelapse_stats": timelapse_stats,
            "active_jobs": active_jobs,
            "recent_completed_jobs": recent_completed_jobs,
            "recent_activities": recent_activities,
            "activity_summary": activity_summary,
        }

    async def get_camera_cards_context(self) -> dict:
        """Get data for camera cards partial."""
        cameras = await self.camera_service.get_active()
        latest_captures = await self.capture_service.get_latest_captures_all()
        capture_stats = await self.capture_stats_service.get_stats()
        recent_failures = await self.capture_service.get_recent_failures(limit=10)

        fetch_settings = await self.settings_service.get_fetch_settings()
        global_intervals = fetch_settings.get_intervals()

        camera_cards = []
        for camera in cameras:
            latest = latest_captures.get(camera.camera_id)
            stats = await self.camera_service.get_stats(camera.camera_id, global_intervals=global_intervals)
            camera_cards.append(
                {
                    "camera": camera,
                    "latest_capture": latest,
                    "has_thumbnail": latest is not None,
                    "stats": stats,
                }
            )

        return {
            "camera_cards": camera_cards,
            "total_cameras": len(cameras),
            "connected_cameras": sum(1 for c in cameras if c.is_connected),
            "total_captures": capture_stats.total_captures,
            "successful_captures": capture_stats.successful_captures,
            "failed_captures": capture_stats.failed_captures,
            "recent_failures": recent_failures,
        }

    async def get_activity_feed_context(self, limit: int = 20) -> dict:
        """Get data for activity feed partial."""
        activities = await self.activity_service.get_recent(limit=limit)
        summary = await self.activity_service.get_summary(hours=24)

        return {
            "activities": activities,
            "summary": summary,
        }

    async def get_stats_cards_context(self) -> dict:
        """Get data for statistics cards partial."""
        capture_stats = await self.capture_stats_service.get_stats()
        timelapse_stats = await self.timelapse_service.get_stats()
        cameras = await self.camera_service.get_active()

        return {
            "capture_stats": capture_stats,
            "timelapse_stats": timelapse_stats,
            "camera_count": len(cameras),
            "connected_count": sum(1 for c in cameras if c.is_connected),
        }
