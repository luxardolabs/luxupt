"""Unit tests for the scheduler frame-source choice (LUXUPT-68).

The scheduler can build each nightly timelapse from live captures (default) or by
fetching the day's frames from Protect's recordings. The form value must thread
through save_scheduler_settings into the persisted payload, and unknown values must
fall back to 'captured' so a malformed POST can never flip the scheduler to an
undefined source.
"""

from types import SimpleNamespace
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


def _service_for_context(
    *,
    live: bool,
    historical: bool,
    saved: ScheduleSource,
) -> TimelapsesViewService:
    settings_service = MagicMock()
    settings_service.get_scheduler_settings = AsyncMock(
        return_value=SimpleNamespace(source=saved)
    )
    settings_service.get_available_schedule_sources = AsyncMock(
        return_value={"live": live, "historical": historical}
    )
    settings_service.get_fetch_settings = AsyncMock(
        return_value=SimpleNamespace(get_intervals=lambda: [60])
    )
    camera_service = MagicMock()
    camera_service.get_all = AsyncMock(return_value=[])
    return TimelapsesViewService(
        db=MagicMock(),
        camera_service=camera_service,
        capture_service=MagicMock(),
        timelapse_service=MagicMock(),
        job_service=MagicMock(),
        settings_service=settings_service,
    )


class TestSchedulerSourcePresentation:
    """The VIEW only decides which radio is pre-checked from the core's availability."""

    @pytest.mark.asyncio
    async def test_both_configured_honors_saved_choice(self) -> None:
        svc = _service_for_context(
            live=True, historical=True, saved=ScheduleSource.HISTORICAL
        )
        ctx = await svc.get_scheduler_context()
        assert ctx["live_available"] and ctx["historical_available"]
        assert ctx["source_selected"] == "historical"

    @pytest.mark.asyncio
    async def test_no_capture_forces_historical(self) -> None:
        svc = _service_for_context(
            live=False, historical=True, saved=ScheduleSource.CAPTURED
        )
        ctx = await svc.get_scheduler_context()
        assert ctx["source_selected"] == "historical"

    @pytest.mark.asyncio
    async def test_no_protect_creds_forces_captured(self) -> None:
        svc = _service_for_context(
            live=True, historical=False, saved=ScheduleSource.HISTORICAL
        )
        ctx = await svc.get_scheduler_context()
        assert ctx["source_selected"] == "captured"

    @pytest.mark.asyncio
    async def test_nothing_configured_selects_nothing(self) -> None:
        svc = _service_for_context(
            live=False, historical=False, saved=ScheduleSource.CAPTURED
        )
        ctx = await svc.get_scheduler_context()
        assert ctx["source_selected"] == ""


class TestScheduleSourceAvailabilityRule:
    """The availability RULE lives in the core service, not the view."""

    @pytest.mark.asyncio
    async def test_live_needs_capture_on_api_base_and_active_camera(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.core import settings_core_service as scs

        svc = scs.SettingsCoreService(db=MagicMock())
        svc.get_effective_api_config = AsyncMock(
            return_value={
                "has_api_key": True,
                "has_base_url": True,
                "has_username": True,
                "has_password": True,
            }
        )
        monkeypatch.setattr(
            scs.camera_crud, "get_active", AsyncMock(return_value=[object()])
        )

        def _fetch(enabled: bool) -> AsyncMock:
            return AsyncMock(return_value=SimpleNamespace(enabled=enabled))

        # Capture ON + creds + camera -> both sources available.
        monkeypatch.setattr(scs.fetch_settings_crud, "get_settings", _fetch(True))
        assert await svc.get_available_schedule_sources() == {
            "live": True,
            "historical": True,
        }

        # Capture OFF (the Protect-recordings workflow) -> live is no longer offered.
        monkeypatch.setattr(scs.fetch_settings_crud, "get_settings", _fetch(False))
        avail = await svc.get_available_schedule_sources()
        assert avail["live"] is False and avail["historical"] is True

        # Capture ON but no active cameras -> live still unavailable.
        monkeypatch.setattr(scs.fetch_settings_crud, "get_settings", _fetch(True))
        monkeypatch.setattr(scs.camera_crud, "get_active", AsyncMock(return_value=[]))
        assert (await svc.get_available_schedule_sources())["live"] is False
