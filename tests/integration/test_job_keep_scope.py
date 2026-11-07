"""Every kind of job keeps or deletes its frames as its row says, and only its own frames.

A historical job, single-day or combined, deletes only the frames and failed captures inside
the windows it fetched, walking the business days of its range; the CLI's jobs record the
scheduler's setting.
"""

import asyncio
import types
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.core.job_core_service as job_mod
from app.core import config
from app.crud import fetch_settings_crud, scheduler_settings_crud
from app.models.capture_model import Capture
from app.models.enum_model import CaptureMethod, CaptureStatus
from app.models.job_model import Job
from app.services.core.job_core_service import JobCoreService, JobProcessor
from app.workers.timelapse_service import TimelapseService

INTERVAL = 60
D1, D2, D3 = date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)
CHICAGO = ZoneInfo("America/Chicago")


async def _scheduler_keeps(db: AsyncSession, *, keep_images: bool) -> None:
    settings = await scheduler_settings_crud.get_settings(db)
    settings.keep_images = keep_images
    await db.flush()


def _dir(day: date) -> Path:
    return (
        config.IMAGE_OUTPUT_PATH
        / "cam_a"
        / f"{INTERVAL}s"
        / f"{day.year:04d}"
        / f"{day.month:02d}"
        / f"{day.day:02d}"
    )


def _ts(day: date, hour: int, minute: int = 0, zone: ZoneInfo | None = None) -> int:
    return int(
        datetime.combine(day, time(hour, minute), tzinfo=zone or UTC).timestamp()
    )


async def _frame(
    db: AsyncSession, ts: int, day: date | None = None, *, failed: bool = False
) -> None:
    day = day or datetime.fromtimestamp(ts, UTC).date()
    folder = _dir(day)
    name = f"cam_a_{ts}.jpg"
    if not failed:
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread((folder / name).write_bytes, b"img")
    db.add(
        Capture(
            camera_id="cam-1",
            camera_safe_name="cam_a",
            file_name=None if failed else name,
            file_path=None if failed else str(folder / name),
            timestamp=ts,
            capture_datetime=datetime.fromtimestamp(ts, UTC),
            capture_date=day,
            interval=INTERVAL,
            status=CaptureStatus.FAILED if failed else CaptureStatus.SUCCESS,
            capture_method=CaptureMethod.API,
        )
    )


async def _left(db: AsyncSession) -> list[int]:
    return sorted(await db.scalars(select(Capture.timestamp)))


class _Camera:
    async def __aenter__(self) -> _Camera:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Fetch:
    """Stands in for the Protect fetch: the frames are already in the day folders."""

    async def run_historical_job(self, _job_id: str) -> object:
        return types.SimpleNamespace(frames_succeeded=1, frames_attempted=1)


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "IMAGE_OUTPUT_PATH", tmp_path / "images")
    monkeypatch.setattr(config, "VIDEO_OUTPUT_PATH", tmp_path / "videos")
    monkeypatch.setattr(config, "THUMBNAIL_CACHE_PATH", tmp_path / "thumbs")
    monkeypatch.setattr(config, "DISPLAY_TIMEZONE", "UTC")

    async def encoded(*_args: object, **_kwargs: object) -> bool:
        return True

    async def finalize(*_args: object) -> None:
        return None

    async def camera_settings(*_args: object) -> object:
        return object()

    monkeypatch.setattr(TimelapseService, "_create_video", encoded)
    monkeypatch.setattr(job_mod, "CameraManager", lambda _settings: _Camera())
    monkeypatch.setattr(job_mod, "HistoricalFetchCoreService", _Fetch)
    monkeypatch.setattr(JobProcessor, "_finalize_successful_job", finalize)
    monkeypatch.setattr(JobProcessor, "_load_camera_manager_settings", camera_settings)


async def _run(
    db: AsyncSession,
    *,
    job_type: str,
    keep_images: bool,
    start_at: datetime,
    end_at: datetime,
    window: tuple[time, time],
) -> None:
    job = await JobCoreService(db).create(
        title=f"cam_a_{job_type}",
        camera_safe_name="cam_a",
        target_date=D1,
        interval=INTERVAL,
        keep_images=keep_images,
        job_type=job_type,
        start_at=start_at,
        end_at=end_at,
        daily_window_start=window[0],
        daily_window_end=window[1],
    )
    await db.flush()
    await JobProcessor()._process_job(job.job_id, D1.isoformat(), "cam_a", INTERVAL)


@pytest.mark.parametrize("keep_images", [False, True])
async def test_a_historical_job_deletes_only_its_windows_frames(
    db: AsyncSession, stack: None, keep_images: bool
) -> None:
    await _scheduler_keeps(db, keep_images=True)
    live = [_ts(D1, h) for h in (1, 2, 3, 16)]
    fetched = _ts(D1, 15, 30)
    for ts in [*live, fetched]:
        await _frame(db, ts)
    await db.flush()

    await _run(
        db,
        job_type="historical",
        keep_images=keep_images,
        start_at=datetime.combine(D1, time(15), tzinfo=UTC),
        end_at=datetime.combine(D1, time(16), tzinfo=UTC),
        window=(time(15), time(16)),
    )

    assert await _left(db) == (live if not keep_images else sorted([*live, fetched]))


async def _run_combined(db: AsyncSession, *, keep_images: bool) -> None:
    await _run(
        db,
        job_type="historical_combined",
        keep_images=keep_images,
        start_at=datetime.combine(D1, time(9), tzinfo=UTC),
        end_at=datetime.combine(D3, time(10), tzinfo=UTC),
        window=(time(9), time(10)),
    )


@pytest.mark.parametrize("keep_images", [False, True])
async def test_a_combined_job_deletes_only_its_daily_windows_frames(
    db: AsyncSession, stack: None, keep_images: bool
) -> None:
    await _scheduler_keeps(db, keep_images=True)
    fetched = [_ts(D1, 9, 30), _ts(D2, 9, 30), _ts(D3, 9, 30)]
    live = [_ts(D1, 15), _ts(D2, 3), _ts(D2, 15), _ts(D3, 3)]
    for ts in [*fetched, *live]:
        await _frame(db, ts)
    await db.flush()

    await _run_combined(db, keep_images=keep_images)

    assert await _left(db) == (live if not keep_images else sorted([*fetched, *live]))
    for ts in live:
        day = datetime.fromtimestamp(ts, UTC).date()
        assert (_dir(day) / f"cam_a_{ts}.jpg").exists()


async def test_a_combined_job_keeps_failed_captures_outside_its_daily_windows(
    db: AsyncSession, stack: None
) -> None:
    await _scheduler_keeps(db, keep_images=True)
    await _frame(db, _ts(D2, 9, 30))
    failed_live = _ts(D2, 15)
    await _frame(db, failed_live, failed=True)
    await db.flush()

    await _run_combined(db, keep_images=False)

    assert await _left(db) == [failed_live]


async def test_a_combined_job_deletes_failed_fetches_of_a_day_with_no_frames(
    db: AsyncSession, stack: None
) -> None:
    await _scheduler_keeps(db, keep_images=True)
    await _frame(db, _ts(D1, 9, 30))
    await _frame(db, _ts(D2, 9, 30), failed=True)
    await db.flush()

    await _run_combined(db, keep_images=False)

    assert await _left(db) == []


@pytest.mark.parametrize("start_hour", [9, 20])
async def test_a_combined_job_walks_business_days(
    db: AsyncSession, stack: None, monkeypatch: pytest.MonkeyPatch, start_hour: int
) -> None:
    # 20:00 in Chicago is the next day in UTC; 09:00 is the same day.
    monkeypatch.setattr(config, "DISPLAY_TIMEZONE", "America/Chicago")
    await _scheduler_keeps(db, keep_images=True)
    for day in (D1, D2):
        await _frame(db, _ts(day, start_hour, 30, CHICAGO), day)
    await db.flush()

    await _run(
        db,
        job_type="historical_combined",
        keep_images=False,
        start_at=datetime.combine(D1, time(start_hour), tzinfo=CHICAGO),
        end_at=datetime.combine(D2, time(start_hour + 1), tzinfo=CHICAGO),
        window=(time(start_hour), time(start_hour + 1)),
    )

    assert await _left(db) == []


class _Cameras:
    async def get_cameras(self) -> list[object]:
        return [types.SimpleNamespace(safe_name="cam_a", id="cam-1")]


@pytest.mark.parametrize("scheduler_keeps", [False, True])
async def test_cli_create_records_the_schedulers_keep_choice(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, scheduler_keeps: bool
) -> None:
    async def run(*_args: object) -> None:
        return None

    monkeypatch.setattr(
        job_mod, "get_job_processor", lambda: types.SimpleNamespace(_process_job=run)
    )
    await _scheduler_keeps(db, keep_images=scheduler_keeps)
    # One interval: the test harness binds one connection, which concurrent jobs cannot share.
    fetch_settings = await fetch_settings_crud.get_settings(db)
    fetch_settings.intervals = [INTERVAL]
    await db.flush()
    service = TimelapseService()
    monkeypatch.setattr(service, "camera_manager", _Cameras(), raising=False)

    await service.create_timelapse_now()

    kept = list(await db.scalars(select(Job.keep_images)))
    assert kept
    assert set(kept) == {scheduler_keeps}
