"""Historical snapshot fetch service.

Drives a Job(job_type='historical'): iterates the requested timestamps with
bounded concurrency, calls ProtectClient.historical_snapshot() for each, and
writes JPEGs into the existing image tree so the downstream pipeline (the
existing TimelapseService._create_video) can render the MP4 unchanged.

Frame path layout (matches the live capture path):
    output/images/{camera.safe_name}/{interval}s/{YYYY}/{MM}/{DD}/{safe_name}_{ts}.jpg

Capture rows are inserted with status='success' or 'failed' so the image
browser and stats services see historical frames the same as live ones.
"""

from __future__ import annotations

import asyncio
import time as time_module
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

import config
from crud.camera_crud import camera_crud
from crud.capture_crud import capture_crud
from crud.fetch_settings_crud import fetch_settings_crud
from crud.job_crud import job_crud
from db.connection import async_session
from logging_config import get_logger
from models.job import Job, JobStatus
from protect_client import ProtectClient, ProtectRequestError
from schemas.capture import CaptureCreate

logger = get_logger(__name__)

DEFAULT_CONCURRENCY = 8
# Recording-write lag — `recording-snapshot?ts=now()` returns 404. Reject any
# requested timestamps within this window of "now" to avoid known failures.
MIN_RECORDING_LAG_SECONDS = 60


@dataclass
class HistoricalFetchResult:
    frames_attempted: int
    frames_succeeded: int
    no_recording: int  # HTTP 404 — gap in Protect's recordings, not our fault
    errors: int  # auth, network, malformed responses
    elapsed_seconds: float

    @property
    def frames_failed(self) -> int:
        return self.no_recording + self.errors


def _expand_timestamps(
    *,
    start_at: datetime,
    end_at: datetime,
    interval_seconds: int,
    daily_start: time | None,
    daily_end: time | None,
) -> list[datetime]:
    """Expand a range + optional daily window into the list of timestamps to fetch."""
    timestamps: list[datetime] = []
    cur_date = start_at.date()
    last_date = end_at.date()
    step = timedelta(seconds=interval_seconds)

    while cur_date <= last_date:
        if daily_start is not None and daily_end is not None:
            window_start = datetime.combine(cur_date, daily_start, tzinfo=start_at.tzinfo)
            window_end_naive = datetime.combine(cur_date, daily_end, tzinfo=start_at.tzinfo)
            # If end-of-day rolls past midnight, push to next day
            if daily_end <= daily_start:
                window_end_naive += timedelta(days=1)
            window_end = window_end_naive
        else:
            window_start = datetime.combine(cur_date, time(0, 0), tzinfo=start_at.tzinfo)
            window_end = datetime.combine(cur_date + timedelta(days=1), time(0, 0), tzinfo=start_at.tzinfo)

        cur = max(window_start, start_at)
        limit = min(window_end, end_at)
        while cur < limit:
            timestamps.append(cur)
            cur += step
        cur_date += timedelta(days=1)
    return timestamps


def _frame_path(safe_name: str, interval: int, ts: datetime) -> Path:
    return (
        config.IMAGE_OUTPUT_PATH
        / safe_name
        / f"{interval}s"
        / ts.strftime("%Y")
        / ts.strftime("%m")
        / ts.strftime("%d")
        / f"{safe_name}_{int(ts.timestamp())}.jpg"
    )


async def _resolve_protect_creds() -> tuple[str, str, str, bool]:
    """(base_url, username, password, verify_ssl) with env-var precedence over DB."""
    async with async_session() as session:
        s = await fetch_settings_crud.get_settings(session)
    base_url = config.UNIFI_PROTECT_BASE_URL or s.base_url or ""
    username = config.UNIFI_PROTECT_USERNAME or s.username or ""
    password = config.UNIFI_PROTECT_PASSWORD or s.password or ""
    verify_ssl = config.UNIFI_PROTECT_VERIFY_SSL if config.UNIFI_PROTECT_BASE_URL else bool(s.verify_ssl)
    return base_url, username, password, verify_ssl


async def _is_canceled(job_id: str) -> bool:
    async with async_session() as session:
        job = await job_crud.get_by_job_id(session, job_id)
    return job is not None and job.status == JobStatus.CANCELLED


class HistoricalJobCanceled(RuntimeError):
    pass


async def run_historical_job(
    job: Job,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> HistoricalFetchResult:
    """Fetch all historical frames for a Job and write them to the image tree.

    Does NOT render video — the caller (JobProcessor) invokes
    TimelapseService._create_video() afterwards.
    """
    if job.job_type not in ("historical", "historical_combined"):
        raise ValueError(f"run_historical_job called on job_type={job.job_type!r}")
    if job.start_at is None or job.end_at is None:
        raise ValueError(f"historical job {job.job_id} missing start_at/end_at")
    if job.end_at <= job.start_at:
        raise ValueError(f"historical job {job.job_id} has end_at <= start_at")

    # Reject ranges that overlap the recording-write-lag window
    cutoff = datetime.now(tz=job.end_at.tzinfo) - timedelta(seconds=MIN_RECORDING_LAG_SECONDS)
    if job.end_at > cutoff:
        raise ValueError(
            f"historical job end_at must be at least {MIN_RECORDING_LAG_SECONDS}s in the past "
            f"(recording-snapshot can 404 on too-recent timestamps)"
        )

    # Look up the camera (need camera_id for Protect, camera.id for capture row)
    async with async_session() as session:
        camera = await camera_crud.get_by_safe_name(session, job.camera_safe_name)
    if camera is None:
        raise ValueError(f"camera {job.camera_safe_name!r} not found")

    timestamps = _expand_timestamps(
        start_at=job.start_at,
        end_at=job.end_at,
        interval_seconds=job.interval,
        daily_start=job.daily_window_start,
        daily_end=job.daily_window_end,
    )
    if not timestamps:
        raise ValueError(f"historical job {job.job_id} produced 0 timestamps")

    # Update the job's frame counts so the UI shows expected total
    async with async_session() as session:
        await job_crud.update_progress(
            session,
            job.job_id,
            progress=0.0,
            message=f"Fetching {len(timestamps)} frames from Protect…",
        )
        await session.commit()

    base_url, username, password, verify_ssl = await _resolve_protect_creds()
    if not (base_url and username and password):
        raise RuntimeError("Protect credentials (base_url + username + password) are not configured")

    semaphore = asyncio.Semaphore(concurrency)
    completed = 0
    succeeded = 0
    no_recording = 0   # HTTP 404 "Recording not found" — gap in Protect's storage
    errors = 0         # everything else (auth, network, malformed response)
    completed_lock = asyncio.Lock()
    started = time_module.time()

    async with ProtectClient(
        base_url=base_url,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    ) as pc:

        async def fetch_one(ts: datetime) -> None:
            nonlocal completed, succeeded, no_recording, errors
            async with semaphore:
                if await _is_canceled(job.job_id):
                    raise HistoricalJobCanceled()

                path = _frame_path(job.camera_safe_name, job.interval, ts)
                path.parent.mkdir(parents=True, exist_ok=True)

                fetch_start = time_module.time()
                status = "success"
                failure_kind: str | None = None  # "no_recording" | "error"
                error_msg: str | None = None
                file_size: int | None = None
                try:
                    jpg = await pc.historical_snapshot(camera.camera_id, ts)
                    path.write_bytes(jpg)
                    file_size = len(jpg)
                except ProtectRequestError as e:
                    status = "failed"
                    err_str = str(e)
                    # Protect returns HTTP 404 "Recording not found" for timestamps that
                    # fall in a gap (camera offline, not recording, etc). That's not an
                    # error per se — track separately so the UI can be honest about it.
                    if "404" in err_str and "Recording not found" in err_str:
                        failure_kind = "no_recording"
                    else:
                        failure_kind = "error"
                    error_msg = err_str[:500]
                except Exception as e:
                    status = "failed"
                    failure_kind = "error"
                    error_msg = f"{type(e).__name__}: {str(e)[:500]}"

                # Record capture row
                try:
                    async with async_session() as session:
                        await capture_crud.create(
                            session,
                            obj_in=CaptureCreate(
                                camera_db_id=camera.id,
                                camera_id=camera.camera_id,
                                camera_safe_name=job.camera_safe_name,
                                timestamp=int(ts.timestamp()),
                                capture_datetime=ts,
                                capture_date=ts.date(),
                                interval=job.interval,
                                status=status,
                                capture_method="protect_historical",
                                file_path=str(path) if status == "success" else None,
                                file_name=path.name if status == "success" else None,
                                file_size=file_size,
                                error_message=error_msg,
                                capture_duration_ms=int((time_module.time() - fetch_start) * 1000),
                            ),
                        )
                        await session.commit()
                except Exception as e:
                    logger.warning(
                        "Failed to record capture row",
                        extra={"job_id": job.job_id, "ts": ts.isoformat(), "error": str(e)},
                    )

                async with completed_lock:
                    completed += 1
                    if status == "success":
                        succeeded += 1
                    elif failure_kind == "no_recording":
                        no_recording += 1
                    else:
                        errors += 1

                    # Throttled progress writes: every ~5 frames or every 10%
                    if completed % max(1, len(timestamps) // 20) == 0 or completed == len(timestamps):
                        parts = [f"{succeeded} ok"]
                        if no_recording:
                            parts.append(f"{no_recording} no-recording")
                        if errors:
                            parts.append(f"{errors} errors")
                        try:
                            async with async_session() as session:
                                await job_crud.update_progress(
                                    session,
                                    job.job_id,
                                    progress=(completed / len(timestamps)) * 100.0,
                                    message=f"Fetched {completed}/{len(timestamps)} ({', '.join(parts)})",
                                    current_image=str(path),
                                )
                                await session.commit()
                        except Exception as e:
                            logger.debug("Progress update failed", extra={"error": str(e)})

        try:
            await asyncio.gather(*(fetch_one(ts) for ts in timestamps))
        except HistoricalJobCanceled:
            logger.info("Historical job canceled mid-flight", extra={"job_id": job.job_id})
            raise

    elapsed = time_module.time() - started
    logger.info(
        "Historical fetch complete",
        extra={
            "job_id": job.job_id,
            "camera": job.camera_safe_name,
            "frames": len(timestamps),
            "succeeded": succeeded,
            "no_recording": no_recording,
            "errors": errors,
            "elapsed_seconds": round(elapsed, 1),
        },
    )

    return HistoricalFetchResult(
        frames_attempted=len(timestamps),
        frames_succeeded=succeeded,
        no_recording=no_recording,
        errors=errors,
        elapsed_seconds=elapsed,
    )
