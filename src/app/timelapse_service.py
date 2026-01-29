"""FFmpeg-based timelapse video generation with progress tracking and thumbnail extraction."""

import asyncio
import json as json_module
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from models.scheduler_settings import SchedulerSettings
    from sqlalchemy.ext.asyncio import AsyncSession

import config
from camera_manager import CameraManager, CameraManagerSettings
from crud import job_crud, timelapse_crud
from crud.fetch_settings_crud import fetch_settings_crud
from crud.scheduler_settings_crud import scheduler_settings_crud
from db.connection import async_session
from logging_config import get_logger
from models.timelapse import Timelapse
from services.capture_cleanup_service import CaptureCleanupService
from sqlalchemy import text
from utils import async_fs

# Module logger
logger = get_logger(__name__)


@dataclass
class EncodingSettings:
    """FFmpeg encoding settings loaded from database - no defaults, must come from DB."""

    frame_rate: int
    crf: int
    preset: str
    pixel_format: str
    ffmpeg_timeout: int

    @classmethod
    def from_scheduler_settings(cls, settings: "SchedulerSettings") -> "EncodingSettings":
        """Create from scheduler settings model."""
        return cls(
            frame_rate=settings.frame_rate,
            crf=settings.crf,
            preset=settings.preset,
            pixel_format=settings.pixel_format,
            ffmpeg_timeout=settings.ffmpeg_timeout,
        )


class JobProgressCallback(Protocol):
    """Protocol for job progress callback interface."""

    async def update_job_progress(
        self,
        job_id: str,
        progress: float,
        status: str,
        message: str,
        current_image: str | None = None,
    ) -> None:
        """Update job progress."""
        ...


class ProgressTracker:
    """Tracks video encoding progress and calculates ETA."""

    def __init__(
        self,
        total_frames: int,
        start_time: float,
        job_key: str,
        camera_name: str,
        interval: int,
        service_instance: "TimelapseService",
        image_files: list[Path] | None = None,
    ) -> None:
        self.total_frames = total_frames
        self.start_time = start_time
        self.job_key = job_key
        self.camera_name = camera_name
        self.interval = interval
        self.service_instance = service_instance
        self.last_update: float = 0
        # Sorted list of image files for thumbnail display
        self.image_files = sorted(image_files) if image_files else []

    async def update_progress(self, current_frame: int) -> None:
        """Update progress and calculate ETA."""
        now = time.time()

        # Calculate percentage with one decimal
        progress_percent = min(round((current_frame / self.total_frames) * 100, 1), 100.0)

        # Calculate ETA
        elapsed = now - self.start_time
        if current_frame > 0:
            frames_per_second = current_frame / elapsed
            remaining_frames = self.total_frames - current_frame
            eta_seconds = remaining_frames / frames_per_second if frames_per_second > 0 else 0
            eta_str = self._format_eta(eta_seconds)
        else:
            eta_str = "calculating..."

        # Throttle all progress updates (UI + DB) to the same interval
        should_update = now - self.last_update > config.PROGRESS_UPDATE_INTERVAL

        if should_update:
            message = (
                f"Encoding: {progress_percent:.1f}% ({current_frame:,}/{self.total_frames:,} frames) • ETA: {eta_str}"
            )

            # Get current image timestamp for thumbnail display
            # Filename format: CameraName_1234567890.jpg -> extract timestamp
            current_image = None
            if self.image_files and 0 <= current_frame < len(self.image_files):
                filename = self.image_files[current_frame].stem  # e.g., "Camera_Name_1234567890"
                # Extract timestamp (last part after underscore)
                parts = filename.rsplit("_", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    current_image = parts[1]  # Just the timestamp

            # Update progress in the database (awaited, not fire-and-forget)
            await self.service_instance._update_progress(
                self.job_key,
                progress_percent,
                message,
                current_frame=current_frame,
                total_frames=self.total_frames,
                current_image=current_image,
            )

            # Debug log
            logger.debug(
                "Encoding progress",
                extra={"camera": self.camera_name, "interval": self.interval, "progress_msg": message},
            )

            self.last_update = now

    def _format_eta(self, seconds: float) -> str:
        """Format ETA in human readable format."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes}m"
        else:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}h {minutes}m"


class TimelapseService:
    """Service for creating time-lapse videos from captured images."""

    def __init__(self) -> None:
        self.camera_manager: CameraManager
        self.running = False
        self.creation_task: asyncio.Task[None] | None = None

        # Semaphore to limit concurrent video creation (set from database on start)
        self._concurrent_jobs: int | None = None
        self.creation_semaphore: asyncio.Semaphore | None = None
        # Lock to prevent race conditions when initializing the semaphore
        self._semaphore_init_lock = asyncio.Lock()

        # For web interface integration
        self._progress_callback: JobProgressCallback | None = None
        self._current_job_id: str | None = None

        # Map job_key to job_id for scheduler-initiated jobs
        self._job_key_to_id: dict[str, str] = {}

        logger.info("Timelapse service initialized")

    async def _ensure_semaphore(self, concurrent_jobs: int) -> asyncio.Semaphore:
        """Ensure semaphore exists with proper concurrency limit. Thread-safe.

        Creates a new semaphore if the concurrent_jobs setting changes.
        Running jobs hold the old semaphore and will finish normally;
        new jobs will use the updated semaphore.
        """
        # Fast path: semaphore exists and value unchanged
        if self.creation_semaphore is not None and self._concurrent_jobs == concurrent_jobs:
            return self.creation_semaphore

        # Need to create or recreate semaphore
        async with self._semaphore_init_lock:
            # Double-check after acquiring lock
            if self.creation_semaphore is None or self._concurrent_jobs != concurrent_jobs:
                if self._concurrent_jobs is not None and self._concurrent_jobs != concurrent_jobs:
                    logger.info(
                        "Updating concurrent timelapse limit",
                        extra={"old": self._concurrent_jobs, "new": concurrent_jobs},
                    )
                else:
                    logger.info("Set concurrent timelapse limit", extra={"concurrent_jobs": concurrent_jobs})
                self._concurrent_jobs = concurrent_jobs
                self.creation_semaphore = asyncio.Semaphore(concurrent_jobs)
            return self.creation_semaphore

    def set_progress_callback(self, callback: JobProgressCallback, job_id: str) -> None:
        """Set progress callback for updates when called from web interface."""
        self._progress_callback = callback
        self._current_job_id = job_id

    async def _load_camera_manager_settings(self) -> CameraManagerSettings:
        """Load settings for CameraManager from database."""
        async with async_session() as session:
            fetch_settings = await fetch_settings_crud.get_settings(session)

            return CameraManagerSettings(
                base_url=config.UNIFI_PROTECT_BASE_URL or fetch_settings.base_url or "",
                api_key=config.UNIFI_PROTECT_API_KEY or fetch_settings.api_key or "",
                verify_ssl=(
                    config.UNIFI_PROTECT_VERIFY_SSL if config.UNIFI_PROTECT_BASE_URL else fetch_settings.verify_ssl
                ),
                request_timeout=fetch_settings.request_timeout,
                rate_limit=fetch_settings.rate_limit,
                rate_limit_buffer=fetch_settings.rate_limit_buffer,
                min_offset_seconds=fetch_settings.min_offset_seconds,
                max_offset_seconds=fetch_settings.max_offset_seconds,
                camera_refresh_interval=fetch_settings.camera_refresh_interval,
            )

    async def start(self) -> None:
        """Start the time-lapse service."""
        if self.running:
            logger.debug("Timelapse service is already running")
            return

        self.running = True

        # Load settings and initialize camera manager
        cm_settings = await self._load_camera_manager_settings()
        self.camera_manager = CameraManager(cm_settings)
        await self.camera_manager.__aenter__()

        try:
            # Start creation task
            self.creation_task = asyncio.create_task(self._run_creation_loop())
            await self.creation_task

        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the time-lapse service."""
        if not self.running:
            return

        self.running = False

        # Cancel creation task
        if self.creation_task and not self.creation_task.done():
            self.creation_task.cancel()
            try:
                await self.creation_task
            except asyncio.CancelledError:
                pass

        # Close camera manager
        if hasattr(self, "camera_manager"):
            await self.camera_manager.__aexit__(None, None, None)

        logger.info("Timelapse service stopped")

    async def _cleanup_stale_jobs(self, db: "AsyncSession") -> None:
        """Mark any running/pending jobs as failed on startup and kill orphaned FFmpeg processes.

        Jobs in these states at startup are stale from a previous run
        that was interrupted (e.g., by a restart).
        """
        # First, get all running jobs to kill their FFmpeg processes
        running_jobs = await job_crud.get_running_with_pids(db)
        for job in running_jobs:
            if job.pid:
                try:
                    os.kill(job.pid, signal.SIGTERM)
                    logger.info(
                        "Killed orphaned FFmpeg process on startup", extra={"job_id": job.job_id, "pid": job.pid}
                    )
                except ProcessLookupError:
                    pass  # Process already finished
                except Exception as e:
                    logger.warning("Failed to kill orphaned FFmpeg", extra={"pid": job.pid, "error": str(e)})

        # Mark all stale jobs as failed and clear PIDs
        count = await job_crud.mark_stale_failed(db, error="Killed on restart")
        await db.commit()

        if count > 0:
            logger.info("Cleaned up stale jobs from previous run", extra={"count": count})

    async def _run_creation_loop(self) -> None:
        """Run the time-lapse creation loop.

        Wakes up every minute to check settings from the database.
        This allows users to change settings and have them take effect quickly.
        """
        last_run_key = None  # Track (date, hour, minute) to avoid duplicate runs while allowing time changes
        db_ready = False  # Track if database is ready

        while self.running:
            try:
                # Fetch settings from database
                async with async_session() as db:
                    try:
                        settings = await scheduler_settings_crud.get_settings(db)
                        await db.commit()  # Commit the auto-created settings row if needed
                        if not db_ready:
                            logger.info("Scheduler connected to database")
                            # Cleanup any stale jobs from previous runs
                            await self._cleanup_stale_jobs(db)
                            db_ready = True
                    except Exception as db_error:
                        # Database not ready yet (table doesn't exist)
                        if "no such table" in str(db_error).lower():
                            if db_ready or not hasattr(self, "_db_wait_logged"):
                                logger.info("Waiting for database to be initialized")
                                self._db_wait_logged = True
                            await asyncio.sleep(5)  # Wait 5 seconds and retry
                            continue
                        raise  # Re-raise other errors

                    # Ensure semaphore is initialized (thread-safe)
                    await self._ensure_semaphore(settings.concurrent_jobs)

                    # Check if scheduler is enabled
                    if not settings.enabled:
                        logger.debug("Scheduler is disabled")
                        await asyncio.sleep(60)
                        continue

                    now = datetime.now()
                    today = now.date()

                    # Check if it's time to run (within the current minute)
                    # Use (date, hour, minute) key so changing run_time allows re-running today
                    run_key = (today, settings.run_time.hour, settings.run_time.minute)
                    should_run = (
                        now.hour == settings.run_time.hour
                        and now.minute == settings.run_time.minute
                        and last_run_key != run_key
                    )

                    if should_run:
                        logger.info("Scheduler triggered", extra={"time": now.strftime("%Y-%m-%d %H:%M:%S")})
                        last_run_key = run_key

                        # Time to create time-lapses
                        start_time = datetime.now()
                        logger.info(
                            "Starting scheduled timelapse creation",
                            extra={"time": start_time.strftime("%Y-%m-%d %H:%M:%S")},
                        )

                        try:
                            # Get enabled cameras and intervals from settings
                            enabled_cameras = settings.enabled_cameras
                            enabled_intervals = settings.enabled_intervals
                            days_ago = settings.days_ago
                            keep_images = settings.keep_images

                            # Create encoding settings from database
                            encoding_settings = EncodingSettings.from_scheduler_settings(settings)

                            await self._create_timelapses_for_date(
                                datetime.now() - timedelta(days=days_ago),
                                enabled_cameras=enabled_cameras,
                                enabled_intervals=enabled_intervals,
                                keep_images=keep_images,
                                encoding_settings=encoding_settings,
                            )

                            # Update last_run_at timestamp
                            await scheduler_settings_crud.update_last_run(db)
                            await db.commit()

                        except Exception as e:
                            logger.error("Error during scheduled timelapse creation", extra={"error": str(e)})

                        end_time = datetime.now()
                        duration_seconds = (end_time - start_time).total_seconds()
                        logger.info(
                            "Scheduled timelapse creation completed",
                            extra={"duration": self._format_duration(duration_seconds)},
                        )
                    else:
                        # Calculate time until next run for logging
                        scheduled_time = now.replace(
                            hour=settings.run_time.hour,
                            minute=settings.run_time.minute,
                            second=0,
                            microsecond=0,
                        )
                        if now >= scheduled_time:
                            scheduled_time += timedelta(days=1)

                        time_until = scheduled_time - now
                        hours_until = time_until.total_seconds() / 3600

                        if now.minute == 0:  # Log every hour
                            logger.debug(
                                "Scheduler waiting for next run",
                                extra={
                                    "next_run": scheduled_time.strftime("%H:%M"),
                                    "hours_until": round(hours_until, 1),
                                },
                            )

            except Exception as e:
                logger.error("Error in scheduler loop", extra={"error": str(e)})

            # Sleep for 60 seconds before checking again
            await asyncio.sleep(60)

    async def _create_timelapses_for_date(
        self,
        target_date: datetime,
        enabled_cameras: list[str] | None = None,
        enabled_intervals: list[int] | None = None,
        keep_images: bool = True,
        encoding_settings: EncodingSettings | None = None,
    ) -> None:
        """Create time-lapses for a specific date.

        Args:
            target_date: The date to create timelapses for.
            enabled_cameras: List of camera safe_names to process, or None for all.
            enabled_intervals: List of intervals to process, or None for all.
            keep_images: Whether to keep source images after successful creation (default True).
            encoding_settings: FFmpeg encoding settings from database (default loads from db).
        """
        # Load settings from database if not provided
        async with async_session() as db:
            settings = await scheduler_settings_crud.get_settings(db)
            if encoding_settings is None:
                encoding_settings = EncodingSettings.from_scheduler_settings(settings)
        # Ensure semaphore is initialized (thread-safe)
        await self._ensure_semaphore(settings.concurrent_jobs)
        # Get list of cameras
        cameras = await self.camera_manager.get_cameras()

        if not cameras:
            logger.info("No cameras available for timelapse creation")
            return

        # Filter cameras if enabled_cameras is specified
        if enabled_cameras:
            cameras = [c for c in cameras if c.safe_name in enabled_cameras]
            if not cameras:
                logger.info("No enabled cameras found for timelapse creation")
                return

        # Determine which intervals to process (load from database if not specified)
        if enabled_intervals:
            intervals_to_process = enabled_intervals
        else:
            # Load intervals from database
            async with async_session() as db:
                fetch_settings = await fetch_settings_crud.get_settings(db)
                intervals_to_process = fetch_settings.get_intervals()

        date_str = target_date.strftime("%Y-%m-%d")
        logger.info(
            "Creating timelapses",
            extra={
                "date": date_str,
                "camera_count": len(cameras),
                "interval_count": len(intervals_to_process),
            },
        )

        # Create tasks for each camera and interval combination
        tasks = []
        for camera in cameras:
            for interval in intervals_to_process:
                task = asyncio.create_task(
                    self._create_timelapse_with_job(
                        camera.safe_name,
                        interval,
                        target_date,
                        keep_images,
                        encoding_settings,
                        camera_id=camera.id,
                    )
                )
                tasks.append(task)

        # Execute all creation tasks
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Count results
            successful = sum(1 for result in results if result is True)
            failed = sum(1 for result in results if isinstance(result, Exception))
            skipped = len(results) - successful - failed

            logger.info(
                "Timelapse creation summary",
                extra={"successful": successful, "failed": failed, "skipped": skipped},
            )

    async def _create_timelapse_with_job(
        self,
        camera_name: str,
        interval: int,
        target_date: datetime,
        keep_images: bool,
        encoding_settings: EncodingSettings,
        *,
        camera_id: str = "",
    ) -> bool | None:
        """Create a timelapse with job tracking for the scheduler.

        Uses the shared JobProcessor to ensure consistent behavior between
        scheduled and manual timelapse creation.
        """
        from services.job_service import get_job_processor

        date_str = target_date.strftime("%Y-%m-%d")
        title = f"{camera_name}_{date_str}_{interval}s"

        # Check if job already exists
        async with async_session() as db:
            existing = await job_crud.get_job_for_camera_date(
                db, camera=camera_name, target_date=target_date.date(), interval=interval
            )
            if existing:
                logger.debug("Job already exists, skipping", extra={"title": title})
                return None

            # Create job record
            job = await job_crud.create_job(
                db,
                title=title,
                camera_safe_name=camera_name,
                camera_id=camera_id,
                target_date=target_date.date(),
                interval=interval,
            )
            await db.commit()
            job_id = job.job_id

        # Use JobProcessor for consistent processing (same as manual jobs)
        # But we need to run it synchronously within our scheduler context
        job_processor = get_job_processor()

        # Process the job directly (not in background since we're already async)
        await job_processor._process_job(job_id, date_str, camera_name, interval, override_deletion=keep_images)

        # Check the result
        async with async_session() as db:
            result_job = await job_crud.get_by_job_id(db, job_id)
            if result_job and result_job.status == "completed":
                return True
            elif result_job and result_job.status == "failed":
                return False
            return None

    def _find_image_files(self, images_path: Path, camera_name: str) -> tuple[list[Path], str]:
        """Find image files for a camera, checking PNG then JPG. Returns (files, format)."""
        files = list(images_path.glob(f"{camera_name}_*.png"))
        if files:
            return files, "png"
        files = list(images_path.glob(f"{camera_name}_*.jpg"))
        return files, "jpg"

    async def _create_timelapse_for_camera_interval(
        self,
        camera_name: str,
        interval: int,
        target_date: datetime,
        keep_images: bool,
        encoding_settings: EncodingSettings,
        job_id: str | None = None,
        recreate_existing: bool = False,
    ) -> bool | str | None:
        """Create a time-lapse video for a specific camera and interval.

        Concurrency is controlled by JobProcessor._semaphore — callers must
        acquire that semaphore before invoking this method.
        """
        year = target_date.strftime("%Y")
        month = target_date.strftime("%m")
        day = target_date.strftime("%d")

        # Define paths
        images_path = config.IMAGE_OUTPUT_PATH / camera_name / f"{interval}s" / year / month / day
        videos_path = config.VIDEO_OUTPUT_PATH / year / month / camera_name / f"{interval}s"

        # Check if images directory exists and has images
        if not await async_fs.path_exists(images_path):
            logger.debug(
                "No images directory",
                extra={
                    "camera": camera_name,
                    "interval": interval,
                    "date": target_date.strftime("%Y-%m-%d"),
                },
            )
            return None

        # Find image files - check both png (RTSP) and jpg (API) formats in one thread dispatch
        image_files, image_format = await asyncio.to_thread(self._find_image_files, images_path, camera_name)
        if not image_files:
            logger.debug(
                "No images found",
                extra={
                    "camera": camera_name,
                    "interval": interval,
                    "date": target_date.strftime("%Y-%m-%d"),
                },
            )
            return None

        logger.info(
            "Creating timelapse",
            extra={"camera": camera_name, "interval": interval, "image_count": len(image_files)},
        )

        # Create output directory
        await async_fs.path_mkdir(videos_path, parents=True, exist_ok=True)

        # Define output file
        output_filename = f"{camera_name}_{year}{month}{day}_{interval}s.mp4"
        output_path = videos_path / output_filename

        # Check if a completed timelapse record exists in the database
        async with async_session() as db:
            existing = await timelapse_crud.get_by_camera_date_interval(
                db,
                camera=camera_name,
                timelapse_date=target_date.date(),
                interval=interval,
            )
            if existing and existing.status == "completed":
                if recreate_existing:
                    # Delete existing timelapse record and file
                    logger.debug(
                        "Recreating existing timelapse",
                        extra={"camera": camera_name, "interval": interval, "timelapse_id": existing.id},
                    )
                    await timelapse_crud.delete(db, id=existing.id)
                    await db.commit()
                    # Also delete the file if it exists
                    await async_fs.path_unlink(output_path, missing_ok=True)
                else:
                    logger.debug(
                        "Timelapse already exists in database, skipping",
                        extra={"camera": camera_name, "interval": interval},
                    )
                    return "exists"  # Distinct from None (no images) and True (created)

        # Also check if file exists but no DB record (legacy/orphaned files)
        if await async_fs.path_exists(output_path):
            if recreate_existing:
                logger.debug(
                    "Deleting orphaned timelapse file for recreation",
                    extra={"camera": camera_name, "interval": interval},
                )
                await async_fs.path_unlink(output_path)
            else:
                logger.debug(
                    "Timelapse file exists (no DB record), skipping",
                    extra={"camera": camera_name, "interval": interval},
                )
                return "exists"

        # Create time-lapse video using database encoding settings
        success = await self._create_video(
            images_path, output_path, camera_name, interval, encoding_settings, image_format, job_id=job_id
        )

        if success and not keep_images:
            # Delete source images, thumbnails, and database records
            try:
                async with async_session() as db:
                    cleanup_service = CaptureCleanupService(db)
                    result = await cleanup_service.delete_by_filters(
                        camera=camera_name,
                        capture_date=target_date.date(),
                        interval=interval,
                    )
                    await db.commit()
                    # Reclaim freed pages after bulk capture deletion
                    if result["db_records_deleted"] > 0:
                        await db.execute(text("PRAGMA incremental_vacuum(1000)"))
                    logger.info(
                        "Cleanup after successful video creation",
                        extra={
                            "camera": camera_name,
                            "interval": interval,
                            "db_records": result["db_records_deleted"],
                            "files_queued": result["files_to_clean"],
                        },
                    )
            except Exception as e:
                logger.error(
                    "Failed to cleanup after video creation",
                    extra={"camera": camera_name, "interval": interval, "error": str(e)},
                )

        return success

    async def _create_video(
        self,
        images_path: Path,
        output_path: Path,
        camera_name: str,
        interval: int,
        encoding_settings: EncodingSettings,
        image_format: str = "png",
        job_id: str | None = None,
    ) -> bool:
        """Create a video using FFmpeg with progress tracking - uses database encoding settings."""

        start_time = time.time()

        # Generate job_key and register mapping if job_id provided
        job_key = f"{camera_name}_{interval}s_{int(time.time())}"
        if job_id:
            self._job_key_to_id[job_key] = job_id

        # Count total images for progress calculation (sorted for thumbnail tracking)
        image_files = await asyncio.to_thread(lambda: sorted(images_path.glob(f"{camera_name}_*.{image_format}")))
        total_frames = len(image_files)

        if total_frames == 0:
            logger.error("No images found", extra={"camera": camera_name, "interval": interval})
            return False

        # Estimate video duration for better progress tracking
        estimated_video_seconds = total_frames / encoding_settings.frame_rate

        # Build FFmpeg command with progress output - all settings from database
        input_pattern = str(images_path / f"{camera_name}_*.{image_format}")

        ffmpeg_command = [
            "ffmpeg",
            "-y",  # Always overwrite (we check existence before calling this)
            "-loglevel",
            "warning",  # Reduce noise but keep some info
            "-nostats",  # Disable stats on stderr (progress comes from -progress pipe:1)
            "-progress",
            "pipe:1",  # Progress to stdout
            "-r",
            str(encoding_settings.frame_rate),
            "-pattern_type",
            "glob",
            "-i",
            input_pattern,
            "-c:v",
            "libx265",
            "-x265-params",
            f"log-level=0:crf={encoding_settings.crf}",
            "-preset",
            encoding_settings.preset,
            "-pix_fmt",
            encoding_settings.pixel_format,
            "-tag:v",
            "hvc1",
            "-movflags",
            "+faststart",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            str(output_path),
        ]

        logger.info(
            "Starting timelapse encoding",
            extra={
                "camera": camera_name,
                "interval": interval,
                "total_frames": total_frames,
                "estimated_video_seconds": round(estimated_video_seconds, 1),
            },
        )

        # Initial status
        await self._update_progress(job_key, 0, f"Starting encoding of {total_frames} frames...")

        try:
            # Start FFmpeg process
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Store PID in database for process management (killing on cancel)
            await self._store_process_pid(job_key, process.pid)

            # Track progress in background (pass sorted image files for thumbnail display)
            progress_tracker = ProgressTracker(
                total_frames, start_time, job_key, camera_name, interval, self, image_files=image_files
            )

            # Start progress tracker (reads stdout) and stderr drain concurrently
            # Both pipes MUST be read during encoding to prevent pipe buffer deadlock
            progress_task: asyncio.Task[None] | None = None
            stderr_task: asyncio.Task[str] | None = None
            if process.stdout is not None:
                progress_task = asyncio.create_task(self._track_progress(process.stdout, progress_tracker))
            if process.stderr is not None:
                stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))

            # Wait for process completion with timeout
            try:
                returncode = await asyncio.wait_for(process.wait(), timeout=encoding_settings.ffmpeg_timeout)
            except asyncio.TimeoutError:
                # FFmpeg hung - kill the process
                logger.error(
                    "FFmpeg timeout - killing process",
                    extra={
                        "camera": camera_name,
                        "interval": interval,
                        "timeout_seconds": encoding_settings.ffmpeg_timeout,
                    },
                )
                process.kill()
                await process.wait()  # Clean up zombie process
                if progress_task is not None:
                    progress_task.cancel()
                if stderr_task is not None:
                    stderr_task.cancel()
                await self._clear_process_pid(job_key)  # Clear PID on timeout
                error_msg = f"FFmpeg timed out after {encoding_settings.ffmpeg_timeout}s"
                await self._update_progress(job_key, -1, error_msg)  # -1 indicates failure
                return False

            # Stop progress tracking and collect stderr
            if progress_task is not None:
                progress_task.cancel()
            stderr = ""
            if stderr_task is not None:
                stderr = await stderr_task

            end_time = time.time()
            duration_seconds = end_time - start_time

            if returncode == 0:
                # Verify output file
                exists, file_size = await async_fs.file_exists_and_size(str(output_path))
                if exists:
                    formatted_size = self._format_file_size(file_size)

                    # Success!
                    await self._update_progress(job_key, 100, f"Video completed ({formatted_size})")

                    logger.info(
                        "Timelapse completed",
                        extra={
                            "camera": camera_name,
                            "interval": interval,
                            "duration": self._format_duration(duration_seconds),
                            "file_size": formatted_size,
                        },
                    )
                    await self._clear_process_pid(job_key)  # Clear PID on success
                    self._job_key_to_id.pop(job_key, None)  # Cleanup mapping
                    return True
                else:
                    error_msg = "Output file not created or empty"
                    await self._update_progress(job_key, -1, error_msg)  # -1 indicates failure
                    await async_fs.path_unlink(output_path, missing_ok=True)  # Remove partial file
                    await self._clear_process_pid(job_key)  # Clear PID on failure
                    self._job_key_to_id.pop(job_key, None)  # Cleanup mapping
                    return False
            else:
                error_msg = stderr[:200] if stderr else "FFmpeg encoding failed"
                await self._update_progress(job_key, -1, error_msg)  # -1 indicates failure
                logger.error(
                    "FFmpeg failed",
                    extra={
                        "camera": camera_name,
                        "interval": interval,
                        "error": error_msg[:200],
                    },
                )
                await async_fs.path_unlink(output_path, missing_ok=True)  # Remove partial file
                await self._clear_process_pid(job_key)  # Clear PID on FFmpeg error
                self._job_key_to_id.pop(job_key, None)  # Cleanup mapping
                return False

        except Exception as e:
            error_msg = f"Video creation error: {str(e)}"
            await self._update_progress(job_key, -1, error_msg)  # -1 indicates failure
            logger.error(
                "Error creating video",
                extra={"camera": camera_name, "interval": interval, "error": str(e)},
            )
            await async_fs.path_unlink(output_path, missing_ok=True)  # Remove partial file
            await self._clear_process_pid(job_key)  # Clear PID on exception
            self._job_key_to_id.pop(job_key, None)  # Cleanup mapping
            return False

    async def _drain_stderr(self, stderr: asyncio.StreamReader) -> str:
        """Drain stderr to prevent pipe buffer deadlock.

        FFmpeg blocks if stderr pipe buffer fills up (64KB). This task
        reads stderr concurrently during encoding so that never happens.
        Returns collected output for error reporting on failure.
        """
        chunks: list[str] = []
        try:
            while True:
                chunk = await stderr.read(4096)
                if not chunk:
                    break
                chunks.append(chunk.decode("utf-8", errors="ignore"))
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        return "".join(chunks)

    async def _track_progress(self, stdout: asyncio.StreamReader, progress_tracker: ProgressTracker) -> None:
        """Parse FFmpeg progress output and update status."""
        frame_pattern = re.compile(r"frame=\s*(\d+)")

        try:
            buffer = ""
            while True:
                chunk = await stdout.read(1024)
                if not chunk:
                    break

                buffer += chunk.decode("utf-8", errors="ignore")

                # Process complete lines
                lines = buffer.split("\n")
                buffer = lines[-1]  # Keep incomplete line

                for line in lines[:-1]:
                    # Look for frame count
                    frame_match = frame_pattern.search(line)
                    if frame_match:
                        current_frame = int(frame_match.group(1))
                        await progress_tracker.update_progress(current_frame)

        except asyncio.CancelledError:
            # Normal when process completes
            pass
        except Exception as e:
            logger.error("Error tracking FFmpeg progress", extra={"error": str(e)})

    async def _update_progress(
        self,
        job_key: str,
        progress: float,
        message: str,
        current_frame: int | None = None,
        total_frames: int | None = None,
        current_image: str | None = None,
    ) -> None:
        """Update job progress in the database via the progress callback.

        Writes are awaited directly (not fire-and-forget) so they don't pile up
        and exhaust the connection pool. The caller throttles calls to every
        PROGRESS_UPDATE_INTERVAL seconds, so SQLite contention is negligible.
        """
        if (
            hasattr(self, "_progress_callback")
            and self._progress_callback
            and hasattr(self, "_current_job_id")
            and self._current_job_id
        ):
            try:
                status = "completed" if progress == 100 else "failed" if progress < 0 else "running"
                await self._progress_callback.update_job_progress(
                    self._current_job_id, progress, status, message, current_image
                )
            except Exception as e:
                logger.error("Failed to update job progress", extra={"error": str(e)})

    async def _store_process_pid(self, job_key: str, pid: int | None) -> None:
        """Store the FFmpeg process PID in the database for process management."""
        if pid is None:
            return

        # Get job_id from web-initiated job or scheduler-initiated job
        job_id = self._current_job_id or self._job_key_to_id.get(job_key)
        if not job_id:
            return

        try:
            async with async_session() as db:
                await job_crud.update_pid(db, job_id, pid)
                await db.commit()
            logger.debug("Stored FFmpeg PID", extra={"job_id": job_id, "pid": pid})
        except Exception as e:
            logger.warning("Failed to store FFmpeg PID", extra={"job_id": job_id, "error": str(e)})

    async def _clear_process_pid(self, job_key: str) -> None:
        """Clear the FFmpeg process PID from the database after completion."""
        job_id = self._current_job_id or self._job_key_to_id.get(job_key)
        if not job_id:
            return

        try:
            async with async_session() as db:
                await job_crud.clear_pid(db, job_id)
                await db.commit()
        except Exception as e:
            logger.warning("Failed to clear FFmpeg PID", extra={"job_id": job_id, "error": str(e)})

    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        size = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format."""
        hours, remainder = divmod(int(seconds), 3600)
        minutes, secs = divmod(remainder, 60)

        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"

    async def _create_timelapse_record(
        self,
        db: "AsyncSession",
        camera_name: str,
        target_date: datetime,
        interval: int,
        output_path: Path,
        encoding_settings: EncodingSettings,
    ) -> None:
        """Create a timelapse record in the database after successful video creation."""
        if not await async_fs.path_exists(output_path):
            logger.warning("Cannot create timelapse record - file not found", extra={"path": str(output_path)})
            return

        stat_result = await async_fs.path_stat(output_path)
        file_size = stat_result.st_size
        output_filename = output_path.name

        # Probe video metadata
        duration_seconds, resolution, frame_count = await self._probe_video_metadata(
            output_path, encoding_settings.frame_rate, encoding_settings.ffmpeg_timeout
        )

        # Generate thumbnail
        thumbnail_path = await self._generate_thumbnail(output_path, duration_seconds, encoding_settings.ffmpeg_timeout)

        # Create timelapse record
        timelapse = Timelapse(
            camera_id="",
            camera_safe_name=camera_name,
            timelapse_date=target_date.date(),
            interval=interval,
            frame_count=frame_count,
            frame_rate=encoding_settings.frame_rate,
            duration_seconds=duration_seconds,
            file_path=str(output_path),
            file_name=output_filename,
            file_size=file_size,
            resolution=resolution,
            thumbnail_path=thumbnail_path,
            status="completed",
            completed_at=datetime.now(),
        )
        db.add(timelapse)

        logger.info(
            "Created timelapse record",
            extra={"camera": camera_name, "date": target_date.strftime("%Y-%m-%d"), "interval": interval},
        )

    async def _probe_video_metadata(
        self, output_path: Path, frame_rate: int, probe_timeout: int
    ) -> tuple[float, str | None, int]:
        """Probe video file for metadata. Returns (duration, resolution, frame_count)."""
        duration_seconds = 0.0
        resolution = None
        frame_count = 0

        def run_ffprobe() -> subprocess.CompletedProcess[str]:
            """Run ffprobe to extract duration, resolution, and frame count from the video."""
            return subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=probe_timeout,
            )

        try:
            result = await asyncio.to_thread(run_ffprobe)
            if result.returncode == 0:
                probe_data = json_module.loads(result.stdout)

                if "format" in probe_data and "duration" in probe_data["format"]:
                    duration_seconds = float(probe_data["format"]["duration"])

                for stream in probe_data.get("streams", []):
                    if stream.get("codec_type") == "video":
                        width = stream.get("width", 0)
                        height = stream.get("height", 0)
                        if width and height:
                            resolution = f"{width}x{height}"
                        if "nb_frames" in stream:
                            frame_count = int(stream["nb_frames"])
                        elif duration_seconds > 0:
                            frame_count = int(duration_seconds * frame_rate)
                        break
        except Exception as e:
            logger.warning("Could not probe video metadata", extra={"error": str(e)})

        return duration_seconds, resolution, frame_count

    async def _generate_thumbnail(self, output_path: Path, duration_seconds: float, probe_timeout: int) -> str | None:
        """Generate thumbnail from video. Returns thumbnail path or None."""
        thumb_filename = output_path.stem + "_thumb.jpg"
        thumb_path = output_path.parent / thumb_filename

        seek_time = min(1.0, duration_seconds * 0.1) if duration_seconds > 0 else 0

        def run_ffmpeg_thumb() -> subprocess.CompletedProcess[str]:
            """Run ffmpeg to extract a single frame as a JPEG thumbnail."""
            return subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(seek_time),
                    "-i",
                    str(output_path),
                    "-vframes",
                    "1",
                    "-vf",
                    "scale=480:-1",
                    "-q:v",
                    "3",
                    str(thumb_path),
                ],
                capture_output=True,
                text=True,
                timeout=probe_timeout,
            )

        try:
            result = await asyncio.to_thread(run_ffmpeg_thumb)
            if result.returncode == 0 and await async_fs.path_exists(thumb_path):
                logger.info("Generated thumbnail", extra={"path": str(thumb_path)})
                return str(thumb_path)
            else:
                logger.warning("Thumbnail generation failed", extra={"stderr": result.stderr})
        except Exception as e:
            logger.warning("Could not generate thumbnail", extra={"error": str(e)})

        return None

    async def create_timelapse_now(self, days_ago: int = 1) -> None:
        """
        Create time-lapses immediately for testing purposes.

        Args:
            days_ago: Number of days ago to create time-lapses for (default: 1)
        """
        target_date = datetime.now() - timedelta(days=days_ago)

        if not hasattr(self, "camera_manager"):
            cm_settings = await self._load_camera_manager_settings()
            self.camera_manager = CameraManager(cm_settings)
            await self.camera_manager.__aenter__()
            should_cleanup = True
        else:
            should_cleanup = False

        try:
            await self._create_timelapses_for_date(target_date)
        finally:
            if should_cleanup:
                await self.camera_manager.__aexit__(None, None, None)
                del self.camera_manager
