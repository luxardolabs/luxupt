"""A settings save refuses a value the settings read model would refuse.

The pages read settings through validated DTOs (FetchSettingsRead, CameraRead). The save paths
stored raw form values, bounded only by the HTML input's min/max, so one out-of-range save turned
every page that reads it into a 500, the settings panel that could repair it included. An
adversarial pass reproduced it; each case below pairs a refused value with an accepted control.
"""

import logging
import time
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.workers.fetch_service as fetch_service_module
from app.core import config
from app.crud.user_crud import user_crud
from app.models.backup_settings_model import BackupSettings
from app.models.camera_model import Camera
from app.models.enum_model import CaptureMethod
from app.models.fetch_settings_model import FetchSettings
from app.models.scheduler_settings_model import SchedulerSettings
from app.services.core import backup_core_service
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


# ── every bound each settings form shows, refused or accepted, read back from the database ──
async def _signed_in(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    async with maker() as s:
        await user_crud.create_user(
            s, username="bounds-owner", password="a-long-enough-password"
        )
        await s.commit()
    resp = await client.post(
        app.url_path_for("pages.login_submit"),
        data={"username": "bounds-owner", "password": "a-long-enough-password"},
    )
    assert resp.status_code in (200, 302, 303)


async def _stored(
    maker: async_sessionmaker[AsyncSession], model: type, column: str
) -> object:
    async with maker() as s:
        row = (await s.execute(select(model))).scalars().first()
        value = getattr(row, column)
        await s.rollback()
        return value


FETCH_BOUNDS = [
    # (field, refused, accepted, stored value for the accepted one)
    ("max_retries", "11", "10", 10),
    ("max_retries", "-1", "0", 0),
    ("retry_delay", "0", "1", 1),
    ("retry_delay", "31", "30", 30),
    ("request_timeout", "0", "5", 5),
    ("request_timeout", "121", "120", 120),
    ("rate_limit", "0", "1", 1),
    ("rate_limit", "101", "100", 100),
    ("rate_limit_buffer", "0.05", "0.1", 0.1),
    ("rate_limit_buffer", "1.5", "1.0", 1.0),
    ("rate_limit_buffer", "inf", "0.5", 0.5),
    ("rate_limit_buffer", "nan", "0.9", 0.9),
    ("min_offset_seconds", "0", "1", 1),
    ("min_offset_seconds", "61", "60", 60),
    ("max_offset_seconds", "301", "300", 300),
    ("camera_refresh_interval", "59", "60", 60),
    ("camera_refresh_interval", "3601", "3600", 3600),
    ("png_compression_level", "10", "9", 9),
    ("png_compression_level", "-1", "0", 0),
    ("rtsp_capture_timeout", "0", "5", 5),
    ("rtsp_capture_timeout", "61", "60", 60),
    ("rtsp_output_format", "jpeg-xl", "jpg", "jpg"),
    ("default_rtsp_quality", "ultra", "low", "low"),
    ("default_capture_method", "protect_historical", "rtsp", "rtsp"),
    ("intervals", "4", "5", [5]),
    ("intervals", "3601", "3600", [3600]),
]


@pytest.mark.parametrize(("field", "refused", "accepted", "stored"), FETCH_BOUNDS)
async def test_fetch_settings_bound(
    client: AsyncClient,
    durable_db: async_sessionmaker[AsyncSession],
    field: str,
    refused: str,
    accepted: str,
    stored: object,
) -> None:
    await _signed_in(client, durable_db)
    before = await _stored(durable_db, FetchSettings, field)
    resp = await client.post(
        app.url_path_for("cameras.save_fetch_settings"),
        data={**FETCH_FORM, field: refused},
    )
    assert resp.status_code < 500 and "Settings saved" not in resp.text
    assert await _stored(durable_db, FetchSettings, field) == before

    resp = await client.post(
        app.url_path_for("cameras.save_fetch_settings"),
        data={**FETCH_FORM, field: accepted},
    )
    assert "Settings saved" in resp.text, resp.text[:300]
    assert await _stored(durable_db, FetchSettings, field) == stored


@pytest.mark.parametrize("field", ["enabled", "verify_ssl", "high_quality_snapshots"])
async def test_unchecking_a_fetch_toggle_turns_it_off(
    client: AsyncClient, durable_db: async_sessionmaker[AsyncSession], field: str
) -> None:
    await _signed_in(client, durable_db)
    await client.post(
        app.url_path_for("cameras.save_fetch_settings"),
        data={**FETCH_FORM, field: "true"},
    )
    assert await _stored(durable_db, FetchSettings, field) is True
    unchecked = {
        k: v for k, v in FETCH_FORM.items() if k != field
    }  # the browser omits it
    resp = await client.post(
        app.url_path_for("cameras.save_fetch_settings"), data=unchecked
    )
    assert "Settings saved" in resp.text, resp.text[:300]
    assert await _stored(durable_db, FetchSettings, field) is False


@pytest.mark.parametrize("buffer", ["inf", "nan"])
async def test_a_non_finite_buffer_is_refused_without_logging_the_password(
    client: AsyncClient,
    durable_db: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
    buffer: str,
) -> None:
    await _signed_in(client, durable_db)
    secret = "Typed-New-Pw-123"
    with caplog.at_level(logging.DEBUG):
        resp = await client.post(
            app.url_path_for("cameras.save_fetch_settings"),
            data={
                **FETCH_FORM,
                "rate_limit_buffer": buffer,
                "username": "u",
                "password": secret,
            },
        )
    assert secret not in caplog.text
    assert secret not in resp.text
    for name in ("system.system_page", "cameras.fetch_settings_panel"):
        page = await client.get(app.url_path_for(name))
        assert page.status_code < 500, (name, page.status_code)


CAMERA_BOUNDS = [
    ("capture_method", "protect_historical", "rtsp", CaptureMethod.RTSP),
    ("rtsp_quality", "x" * 17, "medium", "medium"),
    ("enabled_intervals", "4", "60", [60]),
]


@pytest.mark.parametrize(("field", "refused", "accepted", "stored"), CAMERA_BOUNDS)
async def test_camera_settings_bound(
    client: AsyncClient,
    durable_db: async_sessionmaker[AsyncSession],
    field: str,
    refused: str,
    accepted: str,
    stored: object,
) -> None:
    await _signed_in(client, durable_db)
    async with durable_db() as s:
        s.add(
            Camera(
                camera_id="bounds-cam",
                name="Bounds Cam",
                safe_name="bounds_cam",
                is_active=True,
                is_connected=True,
                capture_method=CaptureMethod.AUTO,
                rtsp_quality="high",
            )
        )
        await s.commit()
    form = {"capture_method": "auto", "rtsp_quality": "high", "is_active": "true"}
    url = app.url_path_for("cameras.save_camera_settings", camera_id="bounds-cam")
    before = await _stored(durable_db, Camera, field)
    await client.post(url, data={**form, field: refused})
    assert await _stored(durable_db, Camera, field) == before
    resp = await client.post(url, data={**form, field: accepted})
    assert "Settings saved" in resp.text, resp.text[:300]
    assert await _stored(durable_db, Camera, field) == stored


SCHEDULER_FORM = {
    "enabled": "true",
    "run_time": "01:00",
    "days_ago": "1",
    "source": "captured",
    "concurrent_jobs": "2",
}
SCHEDULER_BOUNDS = [
    ("days_ago", "0", "1", 1),
    ("days_ago", "31", "30", 30),
    ("concurrent_jobs", "0", "1", 1),
    ("concurrent_jobs", "9", "8", 8),
    ("frame_rate", "121", "120", 120),
    ("frame_rate", "0", "1", 1),
    ("crf", "52", "51", 51),
    ("ffmpeg_timeout", "299", "300", 300),
    ("ffmpeg_timeout", "86401", "86400", 86400),
    ("preset", "turbo", "slow", "slow"),
    ("pixel_format", "bgr24", "rgb24", "rgb24"),
    ("source", "bogus", "historical", "historical"),
    ("run_time", "25:99", "02:30", None),
]


@pytest.mark.parametrize(("field", "refused", "accepted", "stored"), SCHEDULER_BOUNDS)
async def test_scheduler_settings_bound(
    client: AsyncClient,
    durable_db: async_sessionmaker[AsyncSession],
    field: str,
    refused: str,
    accepted: str,
    stored: object,
) -> None:
    await _signed_in(client, durable_db)
    url = app.url_path_for("timelapse_scheduler.save_scheduler_settings")
    before = await _stored(durable_db, SchedulerSettings, field)
    resp = await client.post(url, data={**SCHEDULER_FORM, field: refused})
    assert resp.status_code < 500
    assert await _stored(durable_db, SchedulerSettings, field) == before
    resp = await client.post(url, data={**SCHEDULER_FORM, field: accepted})
    assert "Settings saved" in resp.text, resp.text[:300]
    if stored is not None:
        assert await _stored(durable_db, SchedulerSettings, field) == stored


BACKUP_FORM = {
    "enabled": "true",
    "retention": "5",
    "interval_hours": "1",
    "backup_dir": "backups",
}
BACKUP_BOUNDS = [
    ("retention", "0", "1", "retention", 1),
    ("retention", "101", "100", "retention", 100),
    ("interval_hours", "0", "1", "interval", 3600),
    ("interval_hours", "169", "168", "interval", 168 * 3600),
    ("backup_dir", "b" * 257, "b" * 256, "backup_dir", "b" * 256),
]


@pytest.mark.parametrize(
    ("field", "refused", "accepted", "column", "stored"), BACKUP_BOUNDS
)
async def test_backup_settings_bound(
    client: AsyncClient,
    durable_db: async_sessionmaker[AsyncSession],
    field: str,
    refused: str,
    accepted: str,
    column: str,
    stored: object,
) -> None:
    await _signed_in(client, durable_db)
    url = app.url_path_for("system.update_backup_settings")
    before = await _stored(durable_db, BackupSettings, column)
    resp = await client.post(url, data={**BACKUP_FORM, field: refused})
    assert resp.status_code == 400
    assert await _stored(durable_db, BackupSettings, column) == before
    resp = await client.post(url, data={**BACKUP_FORM, field: accepted})
    assert resp.status_code == 200, resp.text[:300]
    assert await _stored(durable_db, BackupSettings, column) == stored


async def test_unchecking_backups_turns_them_off(
    client: AsyncClient, durable_db: async_sessionmaker[AsyncSession]
) -> None:
    await _signed_in(client, durable_db)
    url = app.url_path_for("system.update_backup_settings")
    await client.post(url, data=BACKUP_FORM)
    assert await _stored(durable_db, BackupSettings, "retention") == 5
    await client.post(
        url, data={k: v for k, v in BACKUP_FORM.items() if k != "enabled"}
    )
    assert await _stored(durable_db, BackupSettings, "retention") == 0


@pytest.mark.parametrize("backup_dir", ["/srv/elsewhere", "../app/static", "a/../../x"])
async def test_backup_dir_outside_the_output_dir_is_refused(
    client: AsyncClient, durable_db: async_sessionmaker[AsyncSession], backup_dir: str
) -> None:
    await _signed_in(client, durable_db)
    url = app.url_path_for("system.update_backup_settings")
    resp = await client.post(url, data={**BACKUP_FORM, "backup_dir": backup_dir})
    assert resp.status_code == 400
    assert await _stored(durable_db, BackupSettings, "backup_dir") == "backups"
    resp = await client.post(url, data={**BACKUP_FORM, "backup_dir": "nightly/db"})
    assert resp.status_code == 200, resp.text[:300]
    assert await _stored(durable_db, BackupSettings, "backup_dir") == "nightly/db"


async def test_a_stored_backup_dir_outside_the_output_dir_writes_nothing(
    durable_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A row saved before the form refused it: the backup service still stays inside OUTPUT_DIR.
    out = tmp_path / "output"
    out.mkdir()
    escape = tmp_path / "public"
    monkeypatch.setattr(backup_core_service, "OUTPUT_DIR", out)
    async with durable_db() as s:
        row = (await s.execute(select(BackupSettings))).scalars().one()
        row.backup_dir = str(escape)
        await s.commit()

    assert await backup_core_service.BackupCoreService().backup_now() is None
    assert not escape.exists()


@pytest.mark.parametrize(
    "field",
    ["max_retries", "rate_limit_buffer", "request_timeout", "png_compression_level"],
)
async def test_an_emptied_fetch_number_keeps_the_stored_value(
    client: AsyncClient, durable_db: async_sessionmaker[AsyncSession], field: str
) -> None:
    await _signed_in(client, durable_db)
    before = await _stored(durable_db, FetchSettings, field)
    resp = await client.post(
        app.url_path_for("cameras.save_fetch_settings"), data={**FETCH_FORM, field: ""}
    )
    assert "Settings saved" in resp.text, resp.text[:300]
    assert await _stored(durable_db, FetchSettings, field) == before


async def test_a_refused_fetch_save_names_the_field(
    client: AsyncClient, durable_db: async_sessionmaker[AsyncSession]
) -> None:
    await _signed_in(client, durable_db)
    resp = await client.post(
        app.url_path_for("cameras.save_fetch_settings"),
        data={**FETCH_FORM, "png_compression_level": "12"},
    )
    assert "Settings saved" not in resp.text
    assert "png_compression_level" in resp.text
