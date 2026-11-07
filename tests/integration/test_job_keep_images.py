"""A job keeps or deletes its source images as the job row says, whoever created it.

The scheduler and the on-demand live job record the scheduler's setting on the row when they
create it; the historical form records the choice the user made.
"""

import types
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.core.job_core_service as job_mod
import app.services.views.timelapses_view_service as tvs_mod
from app.crud import scheduler_settings_crud
from app.models.camera_model import Camera
from app.models.job_model import Job
from app.services.core.camera_core_service import CameraCoreService
from app.services.core.job_core_service import JobCoreService, JobProcessor
from app.services.views.timelapses_view_service import TimelapsesViewService
from app.workers.timelapse_service import TimelapseService

DAY = date(2026, 1, 1)


async def _scheduler_keeps(db: AsyncSession, *, keep_images: bool) -> None:
    settings = await scheduler_settings_crud.get_settings(db)
    settings.keep_images = keep_images
    await db.flush()


class _Camera:
    async def __aenter__(self) -> _Camera:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Run _process_job without Protect or ffmpeg; record the keep_images each build got."""
    calls: list[bool] = []

    async def build(*_args: object, keep_images: bool, **_kwargs: object) -> bool:
        calls.append(keep_images)
        return True

    async def finalize(*_args: object) -> None:
        return None

    async def camera_settings(*_args: object) -> object:
        return object()

    monkeypatch.setattr(job_mod, "CameraManager", lambda _settings: _Camera())
    monkeypatch.setattr(
        TimelapseService, "_create_timelapse_for_camera_interval", build
    )
    monkeypatch.setattr(JobProcessor, "_finalize_successful_job", finalize)
    monkeypatch.setattr(JobProcessor, "_load_camera_manager_settings", camera_settings)
    return calls


async def _job(db: AsyncSession, *, keep_images: bool) -> str:
    job = await JobCoreService(db).create(
        title="cam_a_2026-01-01_60s",
        camera_safe_name="cam_a",
        target_date=DAY,
        interval=60,
        keep_images=keep_images,
    )
    await db.flush()
    return job.job_id


@pytest.mark.parametrize("keep_images", [True, False])
async def test_a_job_runs_with_the_keep_choice_on_its_row(
    db: AsyncSession, built: list[bool], keep_images: bool
) -> None:
    await _scheduler_keeps(db, keep_images=not keep_images)
    job_id = await _job(db, keep_images=keep_images)

    await JobProcessor()._process_job(job_id, DAY.isoformat(), "cam_a", 60)

    assert built == [keep_images]


async def test_the_scheduler_records_its_keep_choice_on_the_job(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run(*_args: object) -> None:
        return None

    monkeypatch.setattr(
        job_mod,
        "get_job_processor",
        lambda: types.SimpleNamespace(_process_job=run),
    )

    await TimelapseService()._create_timelapse_with_job(
        "cam_a",
        60,
        datetime(2026, 1, 1, tzinfo=UTC),
        keep_images=False,
        encoding_settings=MagicMock(),
    )

    assert await db.scalar(select(Job.keep_images)) is False


async def test_an_on_demand_job_records_the_schedulers_keep_choice(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        tvs_mod,
        "get_job_processor",
        lambda: types.SimpleNamespace(start_job=lambda *a: calls.append(a)),
    )
    await _scheduler_keeps(db, keep_images=False)
    db.add(
        Camera(
            camera_id="cam-1",
            name="Front Door",
            safe_name="front_door",
            video_mode="default",
            supports_full_hd_snapshot=False,
            state="CONNECTED",
        )
    )
    await db.flush()
    view = TimelapsesViewService(
        db,
        CameraCoreService(db),
        MagicMock(),
        MagicMock(),
        JobCoreService(db),
        MagicMock(),
        MagicMock(),
    )

    result = await view.create_and_start_job(
        camera_id="cam-1", date_str=DAY.isoformat(), interval=60
    )

    assert result["success"] is True
    assert await db.scalar(select(Job.keep_images)) is False
