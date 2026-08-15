"""Unit tests for the scheduler frame-source choice (LUXUPT-68).

The scheduler can build each nightly timelapse from live captures (default) or by
fetching the day's frames from Protect's recordings. The form value must thread
through save_scheduler_settings into the persisted payload, and unknown values must
fall back to 'captured' so a malformed POST can never flip the scheduler to an
undefined source.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.models.enum_model import ScheduleSource
from app.services.views.timelapses_view_service import TimelapsesViewService


def _make_service() -> tuple[TimelapsesViewService, AsyncMock]:
    settings_service = MagicMock()
    settings_service.update_scheduler_settings = AsyncMock()
    svc = TimelapsesViewService(
        db=MagicMock(),
        camera_service=MagicMock(),
        capture_service=MagicMock(),
        timelapse_service=MagicMock(),
        job_service=MagicMock(),
        settings_service=settings_service,
    )
    return svc, settings_service.update_scheduler_settings


async def _save(svc: TimelapsesViewService, source: str) -> None:
    await svc.save_scheduler_settings(
        enabled="true",
        run_time="01:00",
        days_ago=1,
        source=source,
        concurrent_jobs=2,
        keep_images="true",
        recreate_existing="true",
        enabled_cameras=None,
        enabled_intervals=None,
        frame_rate=None,
        crf=None,
        preset=None,
        pixel_format=None,
        ffmpeg_timeout=None,
    )


class TestSchedulerSource:
    @pytest.mark.asyncio
    async def test_historical_source_is_persisted(self) -> None:
        svc, update = _make_service()
        await _save(svc, "historical")
        update.assert_awaited_once()
        payload = update.await_args.args[0]
        assert payload["source"] is ScheduleSource.HISTORICAL

    @pytest.mark.asyncio
    async def test_captured_source_is_persisted(self) -> None:
        svc, update = _make_service()
        await _save(svc, "captured")
        payload = update.await_args.args[0]
        assert payload["source"] is ScheduleSource.CAPTURED

    @pytest.mark.asyncio
    async def test_unknown_source_falls_back_to_captured(self) -> None:
        svc, update = _make_service()
        await _save(svc, "not-a-real-source")
        payload = update.await_args.args[0]
        assert payload["source"] is ScheduleSource.CAPTURED
