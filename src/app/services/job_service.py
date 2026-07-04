"""Job service for timelapse job management and processing."""

import asyncio
import json as json_module
import os
import signal
import subprocess
from datetime import date, datetime, time

import config
from camera_manager import CameraManager, CameraManagerSettings
from crud import job_crud, scheduler_settings_crud
from crud.fetch_settings_crud import fetch_settings_crud
from db.connection import async_session
from logging_config import get_logger
from models.job import Job
from models.timelapse import Timelapse
from sqlalchemy.ext.asyncio import AsyncSession
from timelapse_service import EncodingSettings, TimelapseService
from utils import async_fs

logger = get_logger(__name__)


class JobService:
    """Service for timelapse job management."""

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    async def get_by_id(self, job_id: str) -> Job | None:
        """Get a job by its UUID."""
        return await job_crud.get_by_job_id(self.db, job_id)

    async def exists_for_camera_date(
        self,
        camera: str,
        target_date: date,
        interval: int,
    ) -> Job | None:
        """Check if a job already exists for camera/date/interval."""
        return await job_crud.get_job_for_camera_date(
            self.db,
            camera=camera,
            target_date=target_date,
            interval=interval,
        )

    async def create(
        self,
        *,
        title: str,
        camera_safe_name: str,
        target_date: date,
        interval: int,
        camera_id: str | None = None,
        keep_images: bool = True,
        job_type: str = "live_daily",
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        daily_window_start: time | None = None,
        daily_window_end: time | None = None,
    ) -> Job:
        """Create a new timelapse job (live_daily or historical)."""
        return await job_crud.create_job(
            self.db,
            title=title,
            camera_safe_name=camera_safe_name,
            target_date=target_date,
            interval=interval,
            camera_id=camera_id,
            keep_images=keep_images,
            job_type=job_type,
            start_at=start_at,
            end_at=end_at,
            daily_window_start=daily_window_start,
            daily_window_end=daily_window_end,
        )

    async def get_active(self) -> list[Job]:
        """Get all active (pending or running) jobs."""
        return await job_crud.get_active(self.db)

    async def get_pending(self) -> list[Job]:
        """Get all pending jobs."""
        return await job_crud.get_pending(self.db)

    async def get_running(self) -> list[Job]:
        """Get all running jobs."""
        return await job_crud.get_running(self.db)

    async def get_completed(self, limit: int = config.DEFAULT_PAGE_SIZE) -> list[Job]:
        """Get recently completed jobs."""
        return await job_crud.get_completed(self.db, limit=limit)

    async def start_job(self, job_id: str) -> Job | None:
        """Mark a job as started."""
        return await job_crud.start_job(self.db, job_id)

    async def update_progress(
        self,
        job_id: str,
        *,
        progress: float,
        message: str | None = None,
    ) -> Job | None:
        """Update job progress."""
        return await job_crud.update_progress(
            self.db,
            job_id,
            progress=progress,
            message=message,
        )

    async def complete_job(
        self,
        job_id: str,
        *,
        output_file: str | None = None,
        result_details: dict | None = None,
        total_frames: int | None = None,
    ) -> Job | None:
        """Mark a job as completed."""
        return await job_crud.complete_job(
            self.db,
            job_id,
            output_file=output_file,
            result_details=result_details,
            total_frames=total_frames,
        )

    async def fail_job(self, job_id: str, *, error: str) -> Job | None:
        """Mark a job as failed."""
        return await job_crud.fail_job(self.db, job_id, error=error)

    async def cancel_job(self, job_id: str) -> Job | None:
        """Cancel a job and kill the FFmpeg process if running."""
        # First get the PID before canceling
        pid = await job_crud.get_pid(self.db, job_id)

        # Kill the FFmpeg process if it's running
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info("Killed FFmpeg process", extra={"job_id": job_id, "pid": pid})
            except ProcessLookupError:
                # Process already finished
                logger.debug("FFmpeg process already finished", extra={"job_id": job_id, "pid": pid})
            except PermissionError:
                logger.warning("Permission denied killing FFmpeg", extra={"job_id": job_id, "pid": pid})
            except Exception as e:
                logger.warning("Failed to kill FFmpeg process", extra={"job_id": job_id, "pid": pid, "error": str(e)})

        return await job_crud.cancel_job(self.db, job_id)

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job by its UUID. Returns True if deleted."""
        job = await job_crud.get_by_job_id(self.db, job_id)
        if not job:
            return False
        await job_crud.delete(self.db, id=job.id)
        return True

    async def mark_stale_jobs_failed(self) -> int:
        """Mark all running/pending jobs as failed and kill their processes. Returns count."""
        # First, get all running jobs to kill their FFmpeg processes
        running_jobs = await job_crud.get_running_with_pids(self.db)
        for job in running_jobs:
            if job.pid:
                try:
                    os.kill(job.pid, signal.SIGTERM)
                    logger.info("Killed orphaned FFmpeg process", extra={"job_id": job.job_id, "pid": job.pid})
                except ProcessLookupError:
                    pass  # Process already finished
                except Exception as e:
                    logger.warning("Failed to kill orphaned FFmpeg", extra={"pid": job.pid, "error": str(e)})

        # Now mark all stale jobs as failed and clear PIDs
        return await job_crud.mark_stale_failed(self.db, error="Marked as stale by user")

    async def get_summary(self) -> dict:
        """Get job summary statistics."""
        return await job_crud.get_summary(self.db)


async def get_job_service(db: AsyncSession) -> JobService:
    """Factory function to create JobService instance."""
    return JobService(db)


# =============================================================================
# Job Processing (moved from web_state.py)
# =============================================================================


class JobProcessor:
    """Handles background job processing for timelapse creation."""

    def __init__(self) -> None:
        self._current_job_id: str | None = None
        # Semaphore to limit concurrent jobs (set from database on first job)
        self._concurrent_limit: int | None = None
        self._semaphore: asyncio.Semaphore | None = None
        # Lock to prevent race conditions when initializing the semaphore
        self._semaphore_init_lock = asyncio.Lock()

    async def _ensure_semaphore(self, concurrent_jobs: int) -> asyncio.Semaphore:
        """Ensure semaphore exists with proper concurrency limit. Thread-safe.

        Creates a new semaphore if the concurrent_jobs setting changes.
        Running jobs hold the old semaphore and will finish normally;
        new jobs will use the updated semaphore.
        """
        # Fast path: semaphore exists and value unchanged
        if self._semaphore is not None and self._concurrent_limit == concurrent_jobs:
            return self._semaphore

        # Need to create or recreate semaphore
        async with self._semaphore_init_lock:
            # Double-check after acquiring lock
            if self._semaphore is None or self._concurrent_limit != concurrent_jobs:
                if self._concurrent_limit is not None and self._concurrent_limit != concurrent_jobs:
                    logger.info(
                        "Updating job processor concurrent limit",
                        extra={"old": self._concurrent_limit, "new": concurrent_jobs},
                    )
                else:
                    logger.info("Set job processor concurrent limit", extra={"concurrent_jobs": concurrent_jobs})
                self._concurrent_limit = concurrent_jobs
                self._semaphore = asyncio.Semaphore(concurrent_jobs)
            return self._semaphore

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

    def start_job(
        self, job_id: str, date_str: str, camera: str, interval: int, keep_images: bool | None = None
    ) -> None:
        """Queue a job for processing in the background (respects concurrency limit)."""
        asyncio.create_task(self._process_job(job_id, date_str, camera, interval, keep_images))

    async def update_job_progress(
        self,
        job_id: str,
        progress: float,
        status: str,
        message: str,
        current_image: str | None = None,
    ) -> None:
        """Update job progress in the database.

        Called every PROGRESS_UPDATE_INTERVAL seconds per job (default 15s).
        Writes are awaited directly (not fire-and-forget) so they never pile up
        or exhaust the connection pool.
        """
        try:
            async with async_session() as db:
                await job_crud.update_progress(
                    db,
                    job_id,
                    progress=progress,
                    message=message,
                    current_image=current_image,
                )
                await db.commit()
        except Exception as e:
            logger.warning("Failed to update job progress", extra={"job_id": job_id, "error": str(e)})

    async def _process_job(
        self, job_id: str, date_str: str, camera: str, interval: int, keep_images: bool | None = None
    ) -> None:
        """Process a timelapse job with progress tracking (waits for semaphore).

        Dispatches on Job.job_type:
          - "live_daily" (default): assemble MP4 from existing JPEGs in the image tree
          - "historical": fetch JPEGs from Protect via uiprotect-style recording-snapshot,
            write them into the same image tree, then run the live_daily assembly step.
        """
        # Get settings and ensure semaphore exists (thread-safe)
        async with async_session() as db:
            scheduler_settings = await scheduler_settings_crud.get_settings(db)
            job_obj = await job_crud.get_by_job_id(db, job_id)
        semaphore = await self._ensure_semaphore(scheduler_settings.concurrent_jobs)

        if job_obj is None:
            logger.error("Job not found in DB", extra={"job_id": job_id})
            return

        # Wait for semaphore - job stays pending until we acquire it
        async with semaphore:
            try:
                # Now mark job as running
                async with async_session() as db:
                    await job_crud.start_job(db, job_id)
                    await db.commit()

                # HISTORICAL (single-day or combined-range): pull frames from Protect
                # first, then fall through to assembly. The assembly branch picks the
                # right method based on job_type further down.
                if job_obj.job_type in ("historical", "historical_combined"):
                    from services.historical_fetch_service import (
                        HistoricalJobCanceled,
                        run_historical_job,
                    )

                    try:
                        result = await run_historical_job(job_obj)
                    except HistoricalJobCanceled:
                        logger.info("Historical job canceled", extra={"job_id": job_id})
                        return
                    if result.frames_succeeded == 0:
                        async with async_session() as db:
                            await job_crud.fail_job(
                                db,
                                job_id,
                                error=f"Fetched 0/{result.frames_attempted} frames from Protect",
                            )
                            await db.commit()
                        return

                # Create timelapse service instance
                timelapse_service = TimelapseService()
                timelapse_service.set_progress_callback(self, job_id)

                # Load encoding settings from database
                encoding_settings = EncodingSettings.from_scheduler_settings(scheduler_settings)

                # Determine keep_images: use scheduler setting if not explicitly specified
                if keep_images is None:
                    keep_images = scheduler_settings.keep_images
                elif keep_images and not scheduler_settings.keep_images:
                    await self.update_job_progress(job_id, 5, "running", "Keeping images for this job (job override)")

                # Initialize camera manager with settings from database
                cm_settings = await self._load_camera_manager_settings()
                timelapse_service.camera_manager = CameraManager(cm_settings)
                await timelapse_service.camera_manager.__aenter__()

                try:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d")

                    if job_obj.job_type == "historical_combined":
                        # Multi-day combined assembly — globs across the date range
                        success = await timelapse_service.create_combined_timelapse_for_range(
                            camera,
                            interval,
                            start_date=job_obj.start_at.date() if job_obj.start_at else date_obj.date(),
                            end_date=job_obj.end_at.date() if job_obj.end_at else date_obj.date(),
                            keep_images=keep_images,
                            encoding_settings=encoding_settings,
                            job_id=job_id,
                        )
                    else:
                        success = await timelapse_service._create_timelapse_for_camera_interval(
                            camera,
                            interval,
                            date_obj,
                            keep_images=keep_images,
                            encoding_settings=encoding_settings,
                            recreate_existing=scheduler_settings.recreate_existing,
                        )

                    if success:
                        await self._finalize_successful_job(job_id, camera, date_obj, interval)
                    else:
                        async with async_session() as db:
                            await job_crud.fail_job(db, job_id, error="Timelapse creation failed")
                            await db.commit()

                finally:
                    if hasattr(timelapse_service, "camera_manager") and timelapse_service.camera_manager is not None:
                        await timelapse_service.camera_manager.__aexit__(None, None, None)

            except Exception as e:
                error_msg = str(e) if str(e) else type(e).__name__
                logger.error("Job failed", extra={"job_id": job_id, "error": error_msg})
                try:
                    async with async_session() as db:
                        await job_crud.fail_job(db, job_id, error=error_msg)
                        await db.commit()
                except Exception as db_err:
                    logger.error(
                        "Failed to mark job as failed in database (ghost job may remain as 'running')",
                        extra={"job_id": job_id, "error": str(db_err)},
                    )

    async def _finalize_successful_job(self, job_id: str, camera: str, date_obj: datetime, interval: int) -> None:
        """Finalize a successful job - probe metadata, generate thumbnail, save to DB.

        Filename layout depends on job_type:
          live_daily / historical:   {camera}_{YYYYMMDD}_{interval}s.mp4
          historical_combined:       {camera}_{YYYYMMDD}_to_{YYYYMMDD}_{interval}s.mp4
        """
        # Pull the job upfront so we can branch on job_type
        async with async_session() as db:
            job = await job_crud.get_by_job_id(db, job_id)

        if job is not None and job.job_type == "historical_combined" and job.start_at and job.end_at:
            start_d = job.start_at.date()
            end_d = job.end_at.date()
            year = start_d.strftime("%Y")
            month = start_d.strftime("%m")
            # Include job_id prefix so concurrent jobs with the same range produce different files
            job_short = job.job_id[:8] if job.job_id else "x"
            output_filename = (
                f"{camera}_{start_d.strftime('%Y%m%d')}_to_{end_d.strftime('%Y%m%d')}_{interval}s_{job_short}.mp4"
            )
        else:
            year = date_obj.strftime("%Y")
            month = date_obj.strftime("%m")
            output_filename = f"{camera}_{year}{month}{date_obj.strftime('%d')}_{interval}s.mp4"
        output_path = config.VIDEO_OUTPUT_PATH / year / month / camera / f"{interval}s" / output_filename

        # Single async check for existence + size (one thread dispatch instead of 5+ blocking calls)
        output_exists, file_size_raw = await async_fs.file_exists_and_size(str(output_path))
        file_size = file_size_raw if output_exists else None

        # Load encoding settings from database
        async with async_session() as db:
            scheduler_settings = await scheduler_settings_crud.get_settings(db)
            frame_rate = scheduler_settings.frame_rate
            probe_timeout = scheduler_settings.ffmpeg_timeout  # Use main timeout for probe too

        # Probe video metadata
        duration_seconds = 0.0
        frame_count = 0
        resolution = None
        thumbnail_path = None

        if output_exists:
            # Get video metadata
            duration_seconds, resolution, frame_count = await self._probe_video_metadata(
                output_path, frame_rate, probe_timeout
            )

            # Decode-validation: ffprobe only reads container headers. To catch corrupted
            # encoded streams (e.g. from a concurrent write collision), actually decode
            # the file to null and look for errors on stderr.
            decode_ok = await self._validate_decodable(output_path, probe_timeout)
            if not decode_ok:
                logger.error(
                    "Output file failed decode validation; failing job",
                    extra={"job_id": job_id, "output": str(output_path)},
                )
                # Delete the corrupt file and any partial thumbnail
                try:
                    await async_fs.path_unlink(output_path, missing_ok=True)
                except Exception:
                    pass
                async with async_session() as db:
                    await job_crud.fail_job(
                        db,
                        job_id,
                        error="Output file is corrupted (decode validation failed). The file has been deleted.",
                    )
                    await db.commit()
                return

            # Generate thumbnail
            thumbnail_path = await self._generate_thumbnail(output_path, duration_seconds, probe_timeout)

        # Save to database
        async with async_session() as db:
            # Get job to retrieve camera_id
            job = await job_crud.get_by_job_id(db, job_id)
            camera_id = job.camera_id if job else ""

            await job_crud.complete_job(
                db,
                job_id,
                output_file=str(output_path) if output_exists else None,
                total_frames=frame_count if frame_count > 0 else None,
            )

            timelapse = Timelapse(
                camera_id=camera_id or "",
                camera_safe_name=camera,
                timelapse_date=date_obj.date(),
                # For combined-range jobs, record the last day too so the browser
                # can render the date span instead of pinning it to start_date.
                end_date=(
                    job.end_at.date()
                    if job is not None and job.job_type == "historical_combined" and job.end_at
                    else None
                ),
                interval=interval,
                frame_count=frame_count,
                frame_rate=frame_rate,
                duration_seconds=duration_seconds,
                file_path=str(output_path) if output_exists else None,
                file_name=output_filename,
                file_size=file_size,
                resolution=resolution,
                thumbnail_path=thumbnail_path,
                status="completed",
                completed_at=datetime.now(),
            )
            db.add(timelapse)
            await db.commit()

        logger.info(
            "Created timelapse record",
            extra={"camera": camera, "date": date_obj.strftime("%Y-%m-%d"), "interval": interval},
        )

    async def _validate_decodable(self, output_path: config.Path, timeout: int) -> bool:
        """Decode the file to null and check that ffmpeg doesn't report decoding errors.

        ffprobe only reads container headers, so it can report valid metadata
        for files whose encoded streams are garbled (e.g. from a concurrent
        write collision). This method actually decodes every packet and watches
        stderr for the telltale signatures (`Invalid NAL unit size`,
        `Error submitting packet to decoder`, etc.). Slow on long files but
        cheap enough for our typical 10-100MB outputs.
        """

        def run() -> tuple[int, str]:
            try:
                proc = subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-i", str(output_path),
                        "-f", "null", "-",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return proc.returncode, (proc.stderr or "")
            except subprocess.TimeoutExpired:
                return 1, "decode validation timed out"
            except Exception as e:
                return 1, f"decode validation crashed: {e!r}"

        rc, stderr = await asyncio.to_thread(run)
        if rc != 0 or stderr.strip():
            logger.warning(
                "Decode validation failed",
                extra={"output": str(output_path), "rc": rc, "stderr_head": stderr[:400]},
            )
            return False
        return True

    async def _probe_video_metadata(
        self, output_path: config.Path, frame_rate: int, probe_timeout: int
    ) -> tuple[float, str | None, int]:
        """Probe video file for metadata. Returns (duration, resolution, frame_count)."""
        duration_seconds = 0.0
        resolution = None
        frame_count = 0

        def run_ffprobe() -> subprocess.CompletedProcess:
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

    async def _generate_thumbnail(
        self, output_path: config.Path, duration_seconds: float, probe_timeout: int
    ) -> str | None:
        """Generate thumbnail from video. Returns thumbnail path or None."""
        thumb_filename = output_path.stem + "_thumb.jpg"
        thumb_path = output_path.parent / thumb_filename

        seek_time = min(1.0, duration_seconds * 0.1) if duration_seconds > 0 else 0

        def run_ffmpeg_thumb() -> subprocess.CompletedProcess:
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


# Global job processor instance
_job_processor: JobProcessor | None = None


def get_job_processor() -> JobProcessor:
    """Get or create the global job processor."""
    global _job_processor
    if _job_processor is None:
        _job_processor = JobProcessor()
    return _job_processor
