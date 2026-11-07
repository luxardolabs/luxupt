"""A historical job on a daylight-saving day fetches only inside its window, and a keep-off job
deletes every frame and failed fetch it made.

The real HistoricalFetchCoreService runs against a fake Protect client (the HTTP boundary);
every 4th snapshot fails.
"""

import asyncio
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import TracebackType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.core.historical_fetch_core_service as fetch_mod
import app.services.core.job_core_service as job_mod
from app.core import config
from app.crud import scheduler_settings_crud
from app.models.camera_model import Camera
from app.models.capture_model import Capture
from app.models.enum_model import CaptureMethod
from app.schemas.historical_fetch_schema import HistoricalFetchResult
from app.services.core.historical_fetch_core_service import HistoricalFetchCoreService
from app.services.core.job_core_service import JobCoreService, JobProcessor
from app.workers.timelapse_service import TimelapseService

INTERVAL = 600
CHICAGO = ZoneInfo("America/Chicago")
SPRING = date(2026, 3, 8)  # America/Chicago springs forward 02:00 -> 03:00
FALL = date(2026, 11, 1)  # falls back 02:00 -> 01:00
PLAIN = date(2026, 3, 1)


class _Protect:
    """Fake Protect: records each requested instant; every 4th snapshot fails."""

    requested: list[datetime] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _Protect:
        return self

    async def __aexit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        return None

    async def historical_snapshot(self, _camera_id: str, ts: datetime) -> bytes:
        _Protect.requested.append(ts)
        if len(_Protect.requested) % 4 == 1:
            raise RuntimeError("snapshot failed")
        return b"img"


class _Camera:
    async def __aenter__(self) -> _Camera:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "IMAGE_OUTPUT_PATH", tmp_path / "images")
    monkeypatch.setattr(config, "VIDEO_OUTPUT_PATH", tmp_path / "videos")
    monkeypatch.setattr(config, "THUMBNAIL_CACHE_PATH", tmp_path / "thumbs")
    monkeypatch.setattr(config, "DISPLAY_TIMEZONE", "America/Chicago")
    monkeypatch.setattr(config, "UNIFI_PROTECT_BASE_URL", "https://protect.test")
    monkeypatch.setattr(config, "UNIFI_PROTECT_USERNAME", "user")
    monkeypatch.setattr(config, "UNIFI_PROTECT_PASSWORD", "pass")
    monkeypatch.setattr(fetch_mod, "ProtectClient", _Protect)
    _Protect.requested = []

    async def encoded(*_args: object, **_kwargs: object) -> bool:
        return True

    async def finalize(*_args: object) -> None:
        return None

    async def camera_settings(*_args: object) -> object:
        return object()

    monkeypatch.setattr(TimelapseService, "_create_video", encoded)
    monkeypatch.setattr(job_mod, "CameraManager", lambda _settings: _Camera())
    monkeypatch.setattr(JobProcessor, "_finalize_successful_job", finalize)
    monkeypatch.setattr(JobProcessor, "_load_camera_manager_settings", camera_settings)


async def _job(db: AsyncSession, day: date, start: time, end: time) -> str:
    settings = await scheduler_settings_crud.get_settings(db)
    settings.keep_images = True
    db.add(
        Camera(
            camera_id="cam-1",
            name="Cam A",
            safe_name="cam_a",
            video_mode="default",
            supports_full_hd_snapshot=False,
            state="CONNECTED",
        )
    )
    job = await JobCoreService(db).create(
        title="cam_a_hist",
        camera_safe_name="cam_a",
        target_date=day,
        interval=INTERVAL,
        keep_images=False,
        job_type="historical",
        start_at=datetime.combine(day, start, tzinfo=CHICAGO),
        end_at=datetime.combine(day, end, tzinfo=CHICAGO),
        daily_window_start=start,
        daily_window_end=end,
    )
    await db.flush()
    return job.job_id


@pytest.mark.parametrize(
    ("day", "start", "end"),
    [
        (PLAIN, time(1, 30), time(3, 30)),
        (FALL, time(0, 30), time(2, 30)),
        (SPRING, time(1, 30), time(3, 30)),
        (SPRING, time(2, 30), time(4, 0)),
    ],
)
async def test_a_keep_off_historical_job_deletes_every_frame_it_fetched(
    db: AsyncSession,
    stack: None,
    monkeypatch: pytest.MonkeyPatch,
    day: date,
    start: time,
    end: time,
) -> None:
    job_id = await _job(db, day, start, end)
    # One fetch at a time: the test harness binds one connection.
    fetched = await HistoricalFetchCoreService().run_historical_job(
        job_id, concurrency=1
    )

    class _Fetched:
        async def run_historical_job(self, _job_id: str) -> HistoricalFetchResult:
            return fetched

    monkeypatch.setattr(job_mod, "HistoricalFetchCoreService", _Fetched)
    assert await db.scalar(
        select(Capture.id).where(
            Capture.capture_method == CaptureMethod.PROTECT_HISTORICAL
        )
    )

    await JobProcessor()._process_job(job_id, day.isoformat(), "cam_a", INTERVAL)

    left = list(await db.scalars(select(Capture.timestamp)))
    # Files go after the commit, in a worker thread.
    for _ in range(100):
        files = await asyncio.to_thread(
            lambda: list(config.IMAGE_OUTPUT_PATH.rglob("*.jpg"))
        )
        if not files:
            break
        await asyncio.sleep(0.02)
    assert (left, files) == ([], [])


async def test_the_spring_forward_fetch_stays_inside_its_window(
    db: AsyncSession, stack: None
) -> None:
    job_id = await _job(db, SPRING, time(1, 30), time(3, 30))

    await HistoricalFetchCoreService().run_historical_job(job_id, concurrency=1)

    start_at = datetime.combine(SPRING, time(1, 30), tzinfo=CHICAGO).timestamp()
    end_at = datetime.combine(SPRING, time(3, 30), tzinfo=CHICAGO).timestamp()
    instants = sorted(ts.astimezone(UTC).timestamp() for ts in _Protect.requested)
    # 01:30 CST to 03:30 CDT is one real hour: six 10-minute frames.
    assert len(instants) == 6
    assert all(start_at <= t < end_at for t in instants)
    assert all(
        later - earlier == INTERVAL
        for earlier, later in zip(instants, instants[1:], strict=False)
    )
