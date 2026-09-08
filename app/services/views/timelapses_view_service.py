"""Timelapses view service for preparing timelapse template data."""

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.post_commit import after_commit
from app.logging_config import get_logger
from app.models.enum_model import ScheduleSource
from app.protect_client import ProtectClient
from app.schemas.pagination_schema import build_pagination
from app.services.core.camera_core_service import CameraCoreService
from app.services.core.capture_core_service import CaptureCoreService
from app.services.core.job_core_service import JobCoreService, get_job_processor
from app.services.core.settings_core_service import SettingsCoreService
from app.services.core.timelapse_browser_core_service import TimelapseBrowserCoreService
from app.services.views._camera_options import (
    PIXEL_FORMAT_OPTIONS,
    PRESET_OPTIONS,
    build_camera_options,
    build_date_options,
)

logger = get_logger(__name__)


class TimelapsesViewService:
    """Prepares data for timelapse pages."""

    def __init__(
        self,
        db: AsyncSession,
        camera_service: CameraCoreService,
        capture_service: CaptureCoreService,
        timelapse_service: TimelapseBrowserCoreService,
        job_service: JobCoreService,
        settings_service: SettingsCoreService,
    ):
        """Initialize with core services."""
        self.db = db
        self.camera_service = camera_service
        self.capture_service = capture_service
        self.timelapse_service = timelapse_service
        self.job_service = job_service
        self.settings_service = settings_service

    def end_at_for(
        self, day: date, end_t: time, now_local: datetime, lag_seconds: int = 60
    ) -> datetime:
        """Effective end datetime for a day: clamp to the recording-lag threshold when the day is today.

        Protect's recording-snapshot endpoint 404s on too-recent timestamps, so an end time
        that lands within `lag_seconds` of now is pulled back to now - lag_seconds.
        """
        candidate = datetime.combine(day, end_t).astimezone()
        if day == now_local.date():
            return min(candidate, now_local - timedelta(seconds=lag_seconds))
        return candidate

    async def get_camera_info(self, camera_id: str) -> dict[str, Any] | None:
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

    async def get_stats_context(self) -> dict[str, Any]:
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

    async def get_dates_context(self, camera: str | None = None) -> dict[str, Any]:
        """Get available dates for timelapse creation."""
        if camera:
            available_dates = await self.capture_service.get_available_dates(
                camera=camera
            )
        else:
            available_dates = []
        return {
            "available_dates": available_dates,
            "available_date_options": build_date_options(available_dates),
            "available_date_options": build_date_options(available_dates),
        }

    async def get_intervals_context(
        self,
        camera: str | None = None,
        date_str: str | None = None,
    ) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
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

    async def get_job_context(self, job_id: str) -> dict[str, Any]:
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

    async def get_recently_completed_context(self, limit: int = 8) -> dict[str, Any]:
        """Assemble the context for the recently-completed jobs fragment.

        The router calls the VIEW; the view calls CORE. Reaching `view_service.job_service`
        from the router skipped this seam (fw.no_layer_reach_through).
        """
        return {"completed_jobs": await self.job_service.get_completed(limit=limit)}

    async def cleanup_stale_jobs_and_build_context(self) -> dict[str, Any]:
        """Fail every stale job, then assemble the job-list context that re-renders.

        The view owns the web outcome (fw.no_passthrough_view_service): core does the
        cleanup, the view logs the operational count and assembles the fragment context, so
        the router does not re-query after the mutation.
        """
        count = await self.job_service.mark_stale_jobs_failed()
        logger.info("Cleaned up stale jobs", extra={"count": count})
        return await self.get_jobs_context()

    async def get_scheduler_context(self) -> dict[str, Any]:
        """Get context for scheduler settings panel."""
        settings = await self.settings_service.get_scheduler_settings()
        cameras = await self.camera_service.get_all()
        fetch_settings = await self.settings_service.get_fetch_settings()
        intervals = fetch_settings.get_intervals()

        # Availability is a config-readiness rule owned by the core service; the view only
        # decides how to present it (which radio is enabled/pre-checked).
        available = await self.settings_service.get_available_schedule_sources()
        live_available = available["live"]
        historical_available = available["historical"]

        # Effective selection: honor the saved source when it's usable, else fall back to
        # whichever source IS usable (empty string when neither is configured).
        saved = settings.source
        if saved == ScheduleSource.HISTORICAL and historical_available:
            source_selected = "historical"
        elif saved == ScheduleSource.CAPTURED and live_available or live_available:
            source_selected = "captured"
        elif historical_available:
            source_selected = "historical"
        else:
            source_selected = ""

        return {
            "settings": settings,
            "cameras": cameras,
            "camera_options": build_camera_options(cameras),
            "intervals": intervals,
            "live_available": live_available,
            "historical_available": historical_available,
            "source_selected": source_selected,
            "pixel_format_options": PIXEL_FORMAT_OPTIONS,
            "preset_options": PRESET_OPTIONS,
        }

    async def update_scheduler_settings(self, update_data: dict[str, Any]) -> None:
        """Update scheduler settings."""
        await self.settings_service.update_scheduler_settings(update_data)

    async def save_scheduler_settings(
        self,
        *,
        enabled: str | None,
        run_time: str,
        days_ago: int,
        source: str,
        concurrent_jobs: int,
        keep_images: str | None,
        recreate_existing: str | None,
        enabled_cameras: list[str] | None,
        enabled_intervals: list[str] | None,
        frame_rate: int | None,
        crf: int | None,
        preset: str | None,
        pixel_format: str | None,
        ffmpeg_timeout: int | None,
    ) -> dict[str, Any]:
        """Build the scheduler payload from raw form values and save.

        Returns context for scheduler_result.html.
        """
        # Convert checkbox "on" value to bool (checkbox is present = enabled)
        is_enabled = enabled is not None

        # Frame source for the nightly run; unknown values fall back to captured.
        try:
            source_choice = ScheduleSource(source)
        except ValueError:
            source_choice = ScheduleSource.CAPTURED

        update_data = {
            "enabled": is_enabled,
            # Convert run_time string from form to time object
            "run_time": datetime.strptime(run_time, "%H:%M").time(),
            "days_ago": days_ago,
            "source": source_choice,
            "concurrent_jobs": concurrent_jobs,
            "keep_images": keep_images is not None,
            "recreate_existing": recreate_existing is not None,
            "enabled_cameras": enabled_cameras if enabled_cameras else None,
            # Convert interval strings to integers
            "enabled_intervals": [int(i) for i in enabled_intervals]
            if enabled_intervals
            else None,
            # FFmpeg settings (None means use env var defaults)
            "frame_rate": frame_rate if frame_rate else None,
            "crf": crf if crf is not None else None,  # crf=0 is valid
            "preset": preset if preset else None,
            "pixel_format": pixel_format if pixel_format else None,
            "ffmpeg_timeout": ffmpeg_timeout if ffmpeg_timeout else None,
        }
        await self.update_scheduler_settings(update_data)
        return {
            "success": True,
            "enabled": is_enabled,
            "run_time": update_data["run_time"],
        }

    async def get_lightbox_context(self, timelapse_id: int) -> dict[str, Any]:
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

    async def serve_thumbnail(self, timelapse_id: int) -> FileResponse:
        """Resolve a timelapse thumbnail and build the response for it.

        The view owns the RESPONSE (fw.no_passthrough_view_service): core validates the path
        (traversal protection), the view turns "missing" into a 404 and shapes the
        FileResponse. The router just returns what this hands back.
        """
        thumb_path = await self.timelapse_service.get_thumbnail_path(timelapse_id)
        if not thumb_path:
            raise HTTPException(status_code=404, detail="Thumbnail not found")
        return FileResponse(thumb_path, media_type="image/jpeg")

    async def delete_timelapse_and_build_stats(
        self, timelapse_id: int
    ) -> dict[str, Any]:
        """Delete a timelapse, then assemble the stats context the OOB swap re-renders.

        The view owns the web outcome (fw.no_passthrough_view_service): core does the
        deletion, the view turns "not found" into a 404 and assembles the context the
        fragment needs, so the router neither branches nor re-queries.
        """
        if not await self.timelapse_service.delete_timelapse(timelapse_id):
            raise HTTPException(status_code=404, detail="Timelapse not found")
        return await self.get_stats_context()

    async def check_job_exists(
        self,
        camera_safe_name: str,
        date_str: str,
        interval: int,
    ) -> bool:
        """Check if a job already exists for camera/date/interval."""
        target_date = date.fromisoformat(date_str)
        existing_job = await self.job_service.exists_for_camera_date(
            camera_safe_name,
            target_date,
            interval,
        )
        return existing_job is not None

    def _schedule_kickoff(
        self, job_id: str, date_str: str, camera_safe_name: str, interval: int
    ) -> None:
        """Kick off the JobProcessor for a job only AFTER the request transaction commits, so the
        worker (its own session) reads a durable row (§5e cross-process read). Keeps this view
        transaction-agnostic (fw.no_redundant_commit). Loop-safe: the args are captured per-call,
        not by loop-variable closure."""
        after_commit(
            self.db,
            lambda: get_job_processor().start_job(
                job_id, date_str, camera_safe_name, interval
            ),
        )

    async def create_and_start_job(
        self, *, camera_id: str, date_str: str, interval: int
    ) -> dict[str, Any]:
        """Create a single timelapse job and kick off processing.

        Returns context for create_result.html.
        """
        camera_info = await self.get_camera_info(camera_id)
        if not camera_info:
            return {"success": False, "error": "Camera not found"}
        camera_safe_name = camera_info["safe_name"]

        # Check if job already exists (use safe_name for job lookup since jobs use file paths)
        if await self.check_job_exists(camera_safe_name, date_str, interval):
            return {
                "success": False,
                "error": f"Job already exists for {camera_safe_name} on {date_str} at {interval}s interval",
            }

        title = f"{camera_safe_name}_{date_str}_{interval}s"
        job = await self.job_service.create(
            title=title,
            camera_safe_name=camera_safe_name,
            camera_id=camera_info["camera_id"],
            target_date=date.fromisoformat(date_str),
            interval=interval,
        )
        # Kick off the worker only after get_db commits (§5e): it reads the job from its own
        # session, so the row must be durable; deferring keeps this view transaction-agnostic.
        self._schedule_kickoff(job.job_id, date_str, camera_safe_name, interval)

        return {
            "success": True,
            "job_id": job.job_id,
            "camera": camera_safe_name,
            "date": date_str,
            "interval": interval,
        }

    async def get_historical_panel_context(self) -> dict[str, Any]:
        """Context for the historical-timelapse creation panel.

        One bootstrap call to Protect populates the recording ranges for ALL
        cameras at render time, so the operator can see what dates are
        available per camera before submitting a job.
        """
        cameras = await self.camera_service.get_active()
        yesterday = date.today() - timedelta(days=1)
        scheduler_settings = await self.settings_service.get_scheduler_settings()
        global_recreate = bool(scheduler_settings.recreate_existing)

        camera_ranges: dict[str, dict[str, str | int]] = {}
        range_error: str | None = None
        if cameras:
            (
                base_url,
                username,
                password,
                verify_ssl,
            ) = await self.settings_service.get_protect_credentials()
            if not (base_url and username and password):
                range_error = "Protect credentials are not configured."
            else:
                try:
                    async with ProtectClient(
                        base_url=base_url,
                        username=username,
                        password=password,
                        verify_ssl=verify_ssl,
                    ) as pc:
                        raw_ranges = await pc.get_all_camera_recording_ranges()
                    for cam in cameras:
                        if cam.camera_id in raw_ranges:
                            oldest, newest = raw_ranges[cam.camera_id]
                            oldest_d = oldest.date()
                            newest_d = newest.date()
                            # Default to yesterday if it's in the range, else clamp to range
                            default_d = min(newest_d, date.today() - timedelta(days=1))
                            if default_d < oldest_d:
                                default_d = oldest_d
                            camera_ranges[cam.camera_id] = {
                                "name": cam.name,
                                "oldest": oldest_d.isoformat(),
                                "newest": newest_d.isoformat(),
                                # Full datetime so the form can show users the actual hour/minute
                                # bounds — Protect's recordingStart isn't midnight; pretending it
                                # was the whole day let users pick ranges that hit 404 storms.
                                # Sent as ISO (machine output) and rendered by the client's
                                # fmtDateTime — formatting here would weld in a format AND the
                                # server's zone (fw.strftime_is_display_only).
                                "oldest_full": oldest.isoformat(),
                                "newest_full": newest.isoformat(),
                                "default": default_d.isoformat(),
                                "days": (newest_d - oldest_d).days,
                            }
                except Exception as e:
                    logger.warning(
                        "Bootstrap fetch for recording ranges failed",
                        extra={"error": str(e)},
                    )
                    range_error = f"Could not read recording ranges: {str(e)[:200]}"

        # Union range for the date input bounds (oldest of all, newest of all)
        union_oldest: str | None = None
        union_newest: str | None = None
        default_date: str | None = None
        if camera_ranges:
            union_oldest = min(str(r["oldest"]) for r in camera_ranges.values())
            union_newest = max(str(r["newest"]) for r in camera_ranges.values())
            # Default to yesterday if it's within union, else the newest
            default_d = min(
                date.fromisoformat(union_newest), date.today() - timedelta(days=1)
            )
            if default_d < date.fromisoformat(union_oldest):
                default_d = date.fromisoformat(union_oldest)
            default_date = default_d.isoformat()

        return {
            "cameras": cameras,
            "camera_options": build_camera_options(cameras),
            "camera_ranges": camera_ranges,
            "range_error": range_error,
            "union_oldest": union_oldest,
            "union_newest": union_newest,
            "default_date": default_date,
            "yesterday_iso": yesterday.isoformat(),
            "global_recreate": global_recreate,
        }

    async def create_historical_jobs(
        self,
        *,
        camera_id: str,
        start_date: str,
        end_date: str,
        start_time: str,
        end_time: str,
        interval: str,
        output_mode: str,
        keep_images: str | None,
        recreate_existing: str | None,
    ) -> dict[str, Any]:
        """Validate raw form input and create historical timelapse job(s).

        output_mode='per_day' fans out one job per day in the range; 'combined'
        creates one job spanning the full range (combined-assembly path).
        Returns context for create_result.html.
        """
        try:
            interval_int = int(interval)
            if interval_int < 5 or interval_int > 86400:
                return {
                    "success": False,
                    "error": "Interval must be between 5 and 86400 seconds.",
                }
            start_d = date.fromisoformat(start_date)
            end_d = date.fromisoformat(end_date)
            start_t = time.fromisoformat(start_time)
            end_t = time.fromisoformat(end_time)

            if end_t <= start_t:
                return {"success": False, "error": "End time must be after start time."}
            if end_d < start_d:
                return {
                    "success": False,
                    "error": "End date must be on or after start date.",
                }
            # Recording-write lag: Protect needs ~60s before a frame is in the recording stream.
            # If the end date is in the future entirely, reject. If it's today (or past)
            # with a time that crosses the lag boundary, clamp silently.
            now_local = datetime.now().astimezone()
            if end_d > now_local.date():
                return {"success": False, "error": "End date cannot be in the future."}
            # If end_t for the actual end_d already lands in the past, fine. If end_d is today
            # and end_t pushes into the future, end_at_for will clamp to the recording-lag threshold.
            if (
                self.end_at_for(end_d, end_t, now_local)
                <= datetime.combine(start_d, start_t).astimezone()
            ):
                return {
                    "success": False,
                    "error": (
                        "End is at or before start after applying recording-lag clamp. "
                        "Wait a minute or pick an earlier end time."
                    ),
                }

            camera_info = await self.get_camera_info(camera_id)
            if not camera_info:
                return {"success": False, "error": "Camera not found."}
            camera_safe_name = camera_info["safe_name"]
            keep = keep_images == "true"
            force_recreate = recreate_existing == "true"

            created_jobs: list[str] = []
            skipped_days: list[str] = []  # for reporting
            recreated_jobs: list[str] = []  # job_ids we cancelled+deleted to re-run

            if output_mode == "combined":
                start_at = datetime.combine(start_d, start_t).astimezone()
                end_at = self.end_at_for(end_d, end_t, now_local)
                existing = await self.job_service.get_active_combined_job(
                    camera_safe_name=camera_safe_name,
                    interval=interval_int,
                    start_at=start_at,
                    end_at_min=end_at - timedelta(seconds=120),
                    end_at_max=end_at + timedelta(seconds=120),
                )
                if existing is not None:
                    if not force_recreate:
                        return {
                            "success": False,
                            "error": (
                                f"A combined job for {camera_safe_name} over this range is already running "
                                f"(id {existing.job_id[:8]}). Toggle 'Recreate existing' to replace it."
                            ),
                        }
                    # Recreate: cancel (kills FFmpeg) + delete the existing job before creating
                    # the new one. Folded into the request transaction (flush, get_db commits) —
                    # no handed-session commit (fw.no_redundant_commit). A rollback after the
                    # os.kill leaves a stale row the startup stale-job sweep reconciles, and a
                    # retry re-kills the dead pid harmlessly; there is no unique (camera,date,
                    # interval) constraint, so the same-txn delete-then-insert can't conflict.
                    await self.job_service.cancel_job(existing.job_id)
                    await self.job_service.delete_job(existing.job_id)
                    recreated_jobs.append(existing.job_id)

                # One job spanning the full range
                range_label = f"{start_d.isoformat()}_to_{end_d.isoformat()}"
                title = f"{camera_safe_name}_{range_label}_{interval_int}s_historical_combined"
                job = await self.job_service.create(
                    title=title,
                    camera_safe_name=camera_safe_name,
                    camera_id=camera_info["camera_id"],
                    target_date=start_d,  # earliest date for the existing target_date column
                    interval=interval_int,
                    keep_images=keep,
                    job_type="historical_combined",
                    start_at=start_at,
                    end_at=end_at,
                    daily_window_start=start_t,
                    daily_window_end=end_t,
                )
                created_jobs.append(job.job_id)
                self._schedule_kickoff(
                    job.job_id, start_d.isoformat(), camera_safe_name, interval_int
                )
            else:
                # Fan out one job per day in the range
                day = start_d
                while day <= end_d:
                    date_str = day.isoformat()
                    if await self.check_job_exists(
                        camera_safe_name, date_str, interval_int
                    ):
                        if not force_recreate:
                            skipped_days.append(date_str)
                            day += timedelta(days=1)
                            continue
                        # Find the existing job for this camera/date/interval and remove it
                        existing_job = await self.job_service.exists_for_camera_date(
                            camera_safe_name, day, interval_int
                        )
                        if existing_job is not None:
                            # Cancel (kills FFmpeg) + delete, folded into the request transaction
                            # — no handed-session commit (fw.no_redundant_commit); see the combined
                            # path above for why a rollback here is recoverable.
                            await self.job_service.cancel_job(existing_job.job_id)
                            await self.job_service.delete_job(existing_job.job_id)
                            recreated_jobs.append(existing_job.job_id)
                    start_at = datetime.combine(day, start_t).astimezone()
                    end_at = self.end_at_for(day, end_t, now_local)
                    # Skip days whose end clamps to before/equal-to start (e.g., today before 00:01)
                    if end_at <= start_at:
                        day += timedelta(days=1)
                        continue
                    title = f"{camera_safe_name}_{date_str}_{interval_int}s_historical"
                    job = await self.job_service.create(
                        title=title,
                        camera_safe_name=camera_safe_name,
                        camera_id=camera_info["camera_id"],
                        target_date=day,
                        interval=interval_int,
                        keep_images=keep,
                        job_type="historical",
                        start_at=start_at,
                        end_at=end_at,
                        daily_window_start=start_t,
                        daily_window_end=end_t,
                    )
                    created_jobs.append(job.job_id)
                    self._schedule_kickoff(
                        job.job_id, date_str, camera_safe_name, interval_int
                    )
                    day += timedelta(days=1)

            if not created_jobs:
                err = f"All matching jobs for {camera_safe_name} in this range already exist."
                if skipped_days:
                    err += f" Skipped: {', '.join(skipped_days)}. Toggle 'Recreate existing' to replace them."
                return {"success": False, "error": err}

            # Build a human-readable summary
            if output_mode == "combined":
                date_summary = f"{start_date} → {end_date}"
            else:
                date_summary = f"{start_date} → {end_date} ({len(created_jobs)} jobs)"
                if skipped_days:
                    date_summary += f", skipped {len(skipped_days)} existing"
                if recreated_jobs:
                    date_summary += f", replaced {len(recreated_jobs)}"

            return {
                "success": True,
                "job_id": created_jobs[0] if len(created_jobs) == 1 else None,
                "camera": camera_safe_name,
                "date": date_summary,
                "interval": interval_int,
                "skipped_days": skipped_days,
                "recreated_jobs": recreated_jobs,
            }
        except Exception as e:
            logger.exception(
                "Error creating historical timelapse",
                extra={"error": str(e), "type": type(e).__name__},
            )
            return {"success": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}

    async def get_browser_context(
        self,
        *,
        camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, Any]:
        """Get all data needed for timelapses browser page."""
        # Get filter options first (needed to determine default date)
        cameras = await self.camera_service.get_active()
        available_dates = await self.timelapse_service.get_available_dates(
            camera=camera
        )
        available_intervals = await self.timelapse_service.get_available_intervals(
            camera=camera
        )

        # No date filter ("All Dates") means all dates — do NOT default to the most
        # recent day (that made "All Dates" unreachable). A specific date still filters.
        timelapse_date = date.fromisoformat(date_str) if date_str else None

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

        return {
            "timelapses": timelapses,
            "cameras": cameras,
            "camera_options": build_camera_options(cameras),
            "available_dates": available_dates,
            "available_date_options": build_date_options(available_dates),
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
            "pagination": build_pagination(page=page, per_page=per_page, total=total),
        }

    async def get_jobs_context(self) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        """Get data for timelapse creation form."""
        cameras = await self.camera_service.get_active()

        # Get available dates with captures
        if camera:
            available_dates = await self.capture_service.get_available_dates(
                camera=camera
            )
            available_intervals = await self.capture_service.get_available_intervals(
                camera=camera
            )
        else:
            available_dates = await self.capture_service.get_available_dates()
            available_intervals = await self.capture_service.get_available_intervals()

        return {
            "cameras": cameras,
            "camera_options": build_camera_options(cameras),
            "available_dates": available_dates,
            "available_date_options": build_date_options(available_dates),
            "available_intervals": available_intervals,
            "selected_camera": camera,
        }
