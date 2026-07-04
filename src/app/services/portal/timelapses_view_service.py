"""Timelapses view service for preparing timelapse template data."""

from datetime import date
from pathlib import Path

from models.job import Job

from services.camera_service import CameraService
from services.capture_service import CaptureService
from services.job_service import JobService
from services.settings_service import SettingsService
from services.timelapse_browser_service import TimelapseBrowserService


class TimelapsesViewService:
    """Prepares data for timelapse pages."""

    def __init__(
        self,
        camera_service: CameraService,
        capture_service: CaptureService,
        timelapse_service: TimelapseBrowserService,
        job_service: JobService,
        settings_service: SettingsService,
    ):
        """Initialize with core services."""
        self.camera_service = camera_service
        self.capture_service = capture_service
        self.timelapse_service = timelapse_service
        self.job_service = job_service
        self.settings_service = settings_service

    async def get_camera_info(self, camera_id: str) -> dict | None:
        """Look up camera by ID to get safe_name and other info.

        Args:
            camera_id: The camera UUID

        Returns:
            Dict with camera_id, safe_name, name or None if not found
        """
        camera = await self.camera_service.get_by_id(camera_id)
        if not camera:
            return None
        return {
            "camera_id": camera.camera_id,
            "safe_name": camera.safe_name,
            "name": camera.name,
        }

    async def get_stats_context(self) -> dict:
        """Get timelapse and job statistics for the stats cards."""
        raw_stats = await self.timelapse_service.get_stats()
        job_summary = await self.job_service.get_summary()
        # Pre-calculate values for display
        return {
            "stats": {
                "completed_timelapses": raw_stats.completed_timelapses,
                "pending_timelapses": raw_stats.pending_timelapses,
                "total_minutes": round(raw_stats.total_duration_seconds / 60, 1),
                "storage_gb": round(raw_stats.total_file_size / 1024 / 1024 / 1024, 2),
            },
            "job_stats": job_summary,
        }

    async def get_dates_context(self, camera: str | None = None) -> dict:
        """Get available dates for timelapse creation."""
        if camera:
            available_dates = await self.capture_service.get_available_dates(camera=camera)
        else:
            available_dates = []
        return {"available_dates": available_dates}

    async def get_intervals_context(
        self,
        camera: str | None = None,
        date_str: str | None = None,
    ) -> dict:
        """Get available intervals for timelapse creation."""
        capture_date = date.fromisoformat(date_str) if date_str else None
        if camera:
            available_intervals = await self.capture_service.get_available_intervals(
                camera=camera,
                capture_date=capture_date,
            )
        else:
            available_intervals = []
        return {"available_intervals": available_intervals}

    async def get_preview_context(
        self,
        camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
    ) -> dict:
        """Get preview context for timelapse creation."""
        capture_date = date.fromisoformat(date_str) if date_str else None
        if camera and capture_date and interval:
            image_count = await self.capture_service.count_by_filters(
                camera=camera,
                capture_date=capture_date,
                interval=interval,
            )
            duration_estimate = image_count / 30 if image_count > 0 else 0
        else:
            image_count = 0
            duration_estimate = 0

        return {
            "image_count": image_count,
            "duration_estimate": duration_estimate,
            "camera": camera,
            "date": capture_date,
            "interval": interval,
        }

    async def get_job_context(self, job_id: str) -> dict:
        """Get context for a single job.

        Returns job and action to take:
        - action='render' for running/pending jobs (show the card)
        - action='delete' for completed/failed/cancelled jobs (remove from UI)
        """
        job = await self.job_service.get_by_id(job_id)

        # Determine what action the UI should take
        if not job or job.status in ("completed", "failed", "cancelled"):
            action = "delete"
        else:
            action = "render"

        return {"job": job, "action": action}

    async def cancel_or_delete_job(self, job_id: str) -> tuple[bool, str]:
        """Cancel or delete a job based on its status.

        Returns (success, action) where action is 'cancelled', 'deleted', or 'not_found'.
        """
        job = await self.job_service.get_by_id(job_id)
        if not job:
            return False, "not_found"

        if job.status in ["running", "pending"]:
            await self.job_service.cancel_job(job_id)
            return True, "cancelled"
        else:
            await self.job_service.delete_job(job_id)
            return True, "deleted"

    async def cleanup_stale_jobs(self) -> int:
        """Mark all stale running/pending jobs as failed. Returns count."""
        return await self.job_service.mark_stale_jobs_failed()

    async def get_scheduler_context(self) -> dict:
        """Get context for scheduler settings panel."""
        settings = await self.settings_service.get_scheduler_settings()
        cameras = await self.camera_service.get_all()
        fetch_settings = await self.settings_service.get_fetch_settings()
        intervals = fetch_settings.get_intervals()

        return {
            "settings": settings,
            "cameras": cameras,
            "intervals": intervals,
        }

    async def update_scheduler_settings(self, update_data: dict) -> None:
        """Update scheduler settings."""
        await self.settings_service.update_scheduler_settings(update_data)

    async def get_lightbox_context(self, timelapse_id: int) -> dict:
        """Get lightbox context for video viewing."""
        timelapse = await self.timelapse_service.get_by_id(timelapse_id)
        return {"timelapse": timelapse}

    async def get_video_path(self, timelapse_id: int) -> tuple[str | None, str | None]:
        """Get video file path and filename for a timelapse (with path traversal protection)."""
        # Core service handles path validation
        video_path = await self.timelapse_service.get_video_path(timelapse_id)
        if not video_path:
            return None, None

        filename = await self.timelapse_service.get_video_filename(timelapse_id)
        return video_path, filename or Path(video_path).name

    async def get_thumbnail_path(self, timelapse_id: int) -> str | None:
        """Get thumbnail path for a timelapse (with path traversal protection)."""
        # Core service handles path validation
        return await self.timelapse_service.get_thumbnail_path(timelapse_id)

    async def delete_timelapse(self, timelapse_id: int) -> bool:
        """Delete a timelapse (database record and files). Returns True if deleted."""
        return await self.timelapse_service.delete_timelapse(timelapse_id)

    async def check_job_exists(
        self,
        camera: str,
        date_str: str,
        interval: int,
    ) -> bool:
        """Check if a job already exists for camera/date/interval."""
        target_date = date.fromisoformat(date_str)
        existing_job = await self.job_service.exists_for_camera_date(
            camera,
            target_date,
            interval,
        )
        return existing_job is not None

    async def create_job(
        self,
        *,
        title: str,
        camera_safe_name: str,
        camera_id: str,
        date_str: str,
        interval: int,
    ) -> Job:
        """Create a new timelapse job."""
        target_date = date.fromisoformat(date_str)
        return await self.job_service.create(
            title=title,
            camera_safe_name=camera_safe_name,
            camera_id=camera_id,
            target_date=target_date,
            interval=interval,
        )

    async def get_browser_context(
        self,
        *,
        camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        """Get all data needed for timelapses browser page."""
        # Get filter options first (needed to determine default date)
        cameras = await self.camera_service.get_active()
        available_dates = await self.timelapse_service.get_available_dates(camera=camera)
        available_intervals = await self.timelapse_service.get_available_intervals(camera=camera)

        # Default to most recent date if no date filter provided
        if date_str:
            timelapse_date = date.fromisoformat(date_str)
        elif available_dates:
            # available_dates should be sorted descending (most recent first)
            timelapse_date = available_dates[0]
        else:
            timelapse_date = None

        # Get total count for pagination
        total = await self.timelapse_service.count_by_filters(
            camera=camera,
            timelapse_date=timelapse_date,
            interval=interval,
            status=status,
        )

        # Get timelapses with filters and pagination
        skip = (page - 1) * per_page
        timelapses = await self.timelapse_service.get_by_filters(
            camera=camera,
            timelapse_date=timelapse_date,
            interval=interval,
            status=status,
            skip=skip,
            limit=per_page,
        )

        # Get statistics (pre-calculated for display)
        raw_stats = await self.timelapse_service.get_stats()
        stats = {
            "completed_timelapses": raw_stats.completed_timelapses,
            "pending_timelapses": raw_stats.pending_timelapses,
            "total_minutes": round(raw_stats.total_duration_seconds / 60, 1),
            "storage_gb": round(raw_stats.total_file_size / 1024 / 1024 / 1024, 2),
        }

        # Get job summary for stats cards
        job_stats = await self.job_service.get_summary()

        # Get active and completed jobs for inline job list
        active_jobs = await self.job_service.get_active()
        completed_jobs = await self.job_service.get_completed(limit=8)

        # Split active jobs into running and pending
        running_jobs = [j for j in active_jobs if j.status == "running"]
        pending_jobs = [j for j in active_jobs if j.status == "pending"]

        # Calculate pagination
        total_pages = (total + per_page - 1) // per_page if total > 0 else 1

        return {
            "timelapses": timelapses,
            "cameras": cameras,
            "available_dates": available_dates,
            "available_intervals": available_intervals,
            "stats": stats,
            "job_stats": job_stats,
            "running_jobs": running_jobs,
            "pending_jobs": pending_jobs,
            "completed_jobs": completed_jobs,
            "filters": {
                "camera": camera,
                "date": timelapse_date,
                "interval": interval,
                "status": status,
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "total_count": total,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        }

    async def get_jobs_context(self) -> dict:
        """Get data for jobs panel."""
        active_jobs = await self.job_service.get_active()
        completed_jobs = await self.job_service.get_completed(limit=8)
        summary = await self.job_service.get_summary()
        scheduler_settings = await self.settings_service.get_scheduler_settings()

        # Split active jobs into running and pending
        running_jobs = [j for j in active_jobs if j.status == "running"]
        pending_jobs = [j for j in active_jobs if j.status == "pending"]

        # Cap display columns at 4 for running jobs
        concurrent_jobs = min(scheduler_settings.concurrent_jobs, 4)

        return {
            "running_jobs": running_jobs,
            "pending_jobs": pending_jobs,
            "completed_jobs": completed_jobs,
            "summary": summary,
            "concurrent_jobs": concurrent_jobs,
        }

    async def get_create_timelapse_context(
        self,
        *,
        camera: str | None = None,
    ) -> dict:
        """Get data for timelapse creation form."""
        cameras = await self.camera_service.get_active()

        # Get available dates with captures
        if camera:
            available_dates = await self.capture_service.get_available_dates(camera=camera)
            available_intervals = await self.capture_service.get_available_intervals(camera=camera)
        else:
            available_dates = await self.capture_service.get_available_dates()
            available_intervals = await self.capture_service.get_available_intervals()

        return {
            "cameras": cameras,
            "available_dates": available_dates,
            "available_intervals": available_intervals,
            "selected_camera": camera,
        }

    async def get_camera_timelapses_context(
        self,
        camera_safe_name: str,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> dict:
        """Get timelapses for a specific camera."""
        camera = await self.camera_service.get_by_safe_name(camera_safe_name)

        if not camera:
            return {
                "camera": None,
                "timelapses": [],
                "pagination": {"page": 1, "total_pages": 1, "total_count": 0},
            }

        # Use camera_id for queries
        camera_id = camera.camera_id

        total = await self.timelapse_service.count_by_filters(camera=camera_id, status="completed")

        skip = (page - 1) * per_page
        timelapses = await self.timelapse_service.get_by_filters(
            camera=camera_id,
            status="completed",
            skip=skip,
            limit=per_page,
        )

        total_pages = (total + per_page - 1) // per_page if total > 0 else 1

        return {
            "camera": camera,
            "timelapses": timelapses,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "total_count": total,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        }
