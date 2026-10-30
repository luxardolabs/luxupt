"""A settings save refuses a value the settings read model would refuse.

The pages read settings through validated DTOs (FetchSettingsRead, CameraRead). The save paths
stored raw form values, bounded only by the HTML input's min/max, so one out-of-range save turned
every page that reads it into a 500, the settings panel that could repair it included. An
adversarial pass reproduced it; each case below pairs a refused value with an accepted control.
"""

import logging
import time

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.workers.fetch_service as fetch_service_module
from app.core import config
from app.crud.user_crud import user_crud
from app.models.camera_model import Camera
from app.models.enum_model import CaptureMethod
from app.web.main import app

FETCH_FORM = {
    "enabled": "true",
    "intervals": "60",
    "default_capture_method": "auto",
    "default_rtsp_quality": "high",
    "max_retries": "3",
    "retry_delay": "2",
    "request_timeout": "30",
    "rate_limit": "10",
    "rate_limit_buffer": "0.8",
    "min_offset_seconds": "2",
    "max_offset_seconds": "15",
    "camera_refresh_interval": "300",
    "high_quality_snapshots": "true",
    "rtsp_output_format": "png",
    "png_compression_level": "6",
    "rtsp_capture_timeout": "15",
}
PAGES = ("cameras.cameras_page", "cameras.fetch_settings_panel", "system.system_page")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("png_compression_level", "6"),
        ("png_compression_level", "12"),
        ("rtsp_output_format", "png"),
        ("rtsp_output_format", "jpeg-xl-lossless"),
    ],
)
async def test_fetch_settings_save_keeps_every_page_rendering(
    auth_client: AsyncClient, field: str, value: str
) -> None:
    await auth_client.post(
        app.url_path_for("cameras.save_fetch_settings"),
        data={**FETCH_FORM, field: value},
    )
    for name in PAGES:
        page = await auth_client.get(app.url_path_for(name))
        assert page.status_code < 500, (name, page.status_code)


@pytest.mark.parametrize("rtsp_quality", ["high", "x" * 17])
async def test_camera_settings_save_keeps_the_cameras_page_rendering(
    auth_client: AsyncClient, db: AsyncSession, rtsp_quality: str
) -> None:
    db.add(
        Camera(
            camera_id="save-validation-cam",
            name="Save Validation Cam",
            safe_name="save_validation_cam",
            is_active=True,
            is_connected=True,
            capture_method=CaptureMethod.AUTO,
            rtsp_quality="high",
        )
    )
    await db.flush()
    await auth_client.post(
        app.url_path_for(
            "cameras.save_camera_settings", camera_id="save-validation-cam"
        ),
        data={
            "capture_method": "auto",
            "rtsp_quality": rtsp_quality,
            "is_active": "true",
        },
    )
    page = await auth_client.get(app.url_path_for("cameras.cameras_page"))
    assert page.status_code < 500, page.status_code


@pytest.mark.parametrize("preset", ["medium", "x" * 17])
async def test_scheduler_save_refuses_an_out_of_range_value(
    auth_client: AsyncClient, preset: str
) -> None:
    resp = await auth_client.post(
        app.url_path_for("timelapse_scheduler.save_scheduler_settings"),
        data={
            "run_time": "01:00",
            "days_ago": "1",
            "source": "captured",
            "concurrent_jobs": "1",
            "preset": preset,
        },
    )
    assert resp.status_code < 500, resp.status_code


async def test_fetch_settings_save_keeps_zero_and_logs_no_credential(
    auth_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "S3cret-Protect-Pw"
    with caplog.at_level(logging.INFO):
        resp = await auth_client.post(
            app.url_path_for("cameras.save_fetch_settings"),
            data={
                **FETCH_FORM,
                "max_retries": "0",
                "username": "u",
                "password": secret,
            },
        )
    assert resp.status_code < 500, resp.status_code
    assert "Settings saved" in resp.text, resp.text[:300]
    assert secret not in caplog.text


class _NoCamerasProtect:
    """A reachable Protect console with no cameras, and no network."""

    def __init__(self, *_: object, **__: object) -> None:
        pass

    async def __aenter__(self) -> _NoCamerasProtect:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get_cameras(self, force_refresh: bool = False) -> list[object]:
        return []


async def test_saving_protect_settings_syncs_cameras_without_waiting_on_itself(
    client: AsyncClient,
    durable_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The save commits in its own session and then syncs cameras, which opens another. If the
    # request's own session had begun a transaction first, the sync's session would wait for its
    # write lock until busy_timeout, then fail: "Settings saved, but connection failed."
    monkeypatch.setattr(config, "DATABASE_BUSY_TIMEOUT", 1500)
    monkeypatch.setattr(fetch_service_module, "CameraManager", _NoCamerasProtect)
    async with durable_db() as s:
        await user_crud.create_user(
            s, username="sync-owner", password="a-long-enough-password"
        )
        await s.commit()
    login = await client.post(
        app.url_path_for("pages.login_submit"),
        data={"username": "sync-owner", "password": "a-long-enough-password"},
    )
    assert login.status_code in (200, 302, 303)

    started = time.monotonic()
    resp = await client.post(
        app.url_path_for("cameras.save_fetch_settings"),
        data={
            **FETCH_FORM,
            "base_url": "https://protect.invalid",
            "api_key": "test-api-key",
        },
    )

    assert "Found 0 camera" in resp.text, resp.text[:600]
    assert time.monotonic() - started < 1.0
