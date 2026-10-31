"""No slow call (a camera or Protect request) runs while this process holds the write lock.

Every transaction begins IMMEDIATE, so a session that has run any statement holds the database's
one write lock until it commits. A camera request made inside such a session stalls every page and
the fetch worker for as long as the camera takes to answer. Each test stubs the slow call so that,
while it is in flight, another connection tries to take the lock; the call must find it free.
"""

import os
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.views.cameras_view_service as cameras_view_module
import app.services.views.timelapses_view_service as timelapses_view_module
import app.workers.timelapse_service as timelapse_service_module
from app.clients.camera_manager import Camera, CameraManager, CameraManagerSettings
from app.core import config
from app.crud import capture_crud, timelapse_crud
from app.crud.camera_crud import camera_crud
from app.crud.fetch_settings_crud import fetch_settings_crud
from app.crud.user_crud import user_crud
from app.db.database import seed_singleton_settings
from app.models.enum_model import CaptureMethod, CaptureStatus
from app.schemas.capture_schema import CaptureCreate
from app.services.core.health_core_service import HealthCoreService
from app.utils import async_fs
from app.web.main import app, sync_cameras_to_db
from app.workers.timelapse_service import TimelapseService

LockProbe = Callable[[], Awaitable[bool]]


def _write_lock_free_now() -> bool:
    """The `write_lock_free` probe for synchronous code (a check run in a worker thread)."""
    path = os.environ["TEST_DATABASE_URL"].split("///", 1)[1]
    conn = sqlite3.connect(path, timeout=0.3, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("COMMIT")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


_SETTINGS = CameraManagerSettings(
    base_url="https://protect.invalid",
    api_key="unused",
    verify_ssl=False,
    request_timeout=1,
    rate_limit=10,
    rate_limit_buffer=0.8,
    min_offset_seconds=0,
    max_offset_seconds=0,
    camera_refresh_interval=300,
)


class _ProbingCameraManager(CameraManager):
    """A camera manager whose network calls check the write lock while they are 'in flight'.

    It lists `cameras` and answers every capability probe; nothing reaches a network.
    """

    def __init__(self, probe: LockProbe, cameras: list[Camera]) -> None:
        super().__init__(_SETTINGS)
        self._probe = probe
        self._listed = cameras
        self.lock_free_during_calls: list[bool] = []

    async def get_cameras(self, force_refresh: bool = False) -> list[Camera]:
        self.lock_free_during_calls.append(await self._probe())
        return self._listed

    async def detect_camera_capabilities(self, camera: Camera) -> dict[str, Any]:
        self.lock_free_during_calls.append(await self._probe())
        return {
            "api_max_resolution": "1920x1080",
            "rtsp_max_resolution": "3840x2160",
            "recommended_method": "rtsp",
        }


def _connected_camera(n: int) -> Camera:
    return Camera(
        id=f"cam-{n}",
        name=f"Yard {n}",
        state="CONNECTED",
        mac="",
        is_connected=True,
        is_recording=True,
    )


@pytest.mark.parametrize("route", ["health.health_check", "health.readiness_check"])
async def test_health_probes_ask_the_cameras_outside_a_transaction(
    client: AsyncClient,
    write_lock_free: LockProbe,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    cameras = _ProbingCameraManager(write_lock_free, [])
    monkeypatch.setattr(app.state, "camera_manager", cameras, raising=False)

    resp = await client.get(app.url_path_for(route))

    # 503 is these probes' own "unhealthy" answer (no cameras, bare storage), not a crash.
    assert resp.status_code in (200, 503)
    assert cameras.lock_free_during_calls == [True]


async def test_health_checks_the_storage_paths_outside_a_transaction(
    client: AsyncClient,
    write_lock_free: LockProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_free: list[bool] = []

    def storage(self: HealthCoreService) -> dict[str, Any]:
        lock_free.append(_write_lock_free_now())
        return {"status": "healthy", "message": "ok"}

    monkeypatch.setattr(HealthCoreService, "check_storage", storage)
    monkeypatch.setattr(app.state, "camera_manager", None, raising=False)

    resp = await client.get(app.url_path_for("health.health_check"))

    assert resp.status_code in (200, 503)
    assert lock_free == [True]


async def test_startup_sync_probes_every_new_camera_outside_a_transaction(
    write_lock_free: LockProbe,
    durable_db: async_sessionmaker[AsyncSession],
) -> None:
    discovered = [_connected_camera(1), _connected_camera(2)]
    manager = _ProbingCameraManager(write_lock_free, discovered)

    await sync_cameras_to_db(manager)

    # The listing, then one capability probe per new camera.
    assert manager.lock_free_during_calls == [True, True, True]
    async with durable_db() as db:
        for camera in discovered:
            stored = await camera_crud.get_by_camera_id(db, camera.id)
            assert stored is not None
            assert stored.rtsp_max_resolution == "3840x2160"


async def test_scheduler_waits_for_the_schema_outside_its_failed_transaction(
    write_lock_free: LockProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = TimelapseService()
    lock_free_while_waiting: list[bool] = []

    async def settings_table_not_there_yet(db: AsyncSession) -> object:
        # The read runs (and so begins the transaction) before the table turns out missing.
        await db.execute(text("SELECT 1"))
        raise OperationalError(
            "SELECT * FROM scheduler_settings", {}, Exception("no such table")
        )

    async def wait(seconds: float) -> None:
        lock_free_while_waiting.append(await write_lock_free())
        service.running = False

    monkeypatch.setattr(
        timelapse_service_module.scheduler_settings_crud,
        "get_settings",
        settings_table_not_there_yet,
    )
    monkeypatch.setattr(timelapse_service_module.asyncio, "sleep", wait)

    service.running = True
    await service._run_creation_loop()

    assert lock_free_while_waiting == [True]


# ── request paths: the owner signs in, then a panel action makes the slow call ──────────────────
# These avoid the `db` / `auth_client` fixtures: their open session would itself hold the lock.

_PASSWORD = "a-long-enough-password"


async def _sign_in(
    client: AsyncClient, durable_db: async_sessionmaker[AsyncSession]
) -> None:
    async with durable_db() as db:
        await user_crud.create_user(db, username="owner", password=_PASSWORD)
        await db.commit()
    resp = await client.post(
        app.url_path_for("pages.login_submit"),
        data={"username": "owner", "password": _PASSWORD},
    )
    assert resp.status_code in (302, 303)


async def _seed_protect_and_a_camera(
    durable_db: async_sessionmaker[AsyncSession],
) -> None:
    await seed_singleton_settings()
    async with durable_db() as db:
        await fetch_settings_crud.update_settings(
            db,
            obj_in={
                "base_url": "https://protect.invalid",
                "api_key": "key",
                "username": "viewer",
                "password": "secret-password",
            },
        )
        await camera_crud.upsert_from_dict(
            db,
            data={
                "camera_id": "cam-1",
                "name": "Yard 1",
                "safe_name": "yard_1",
                "mac": "",
                "state": "CONNECTED",
                "is_connected": True,
                "is_recording": True,
                "is_active": True,
                "capture_method": "rtsp",
                "rtsp_quality": "high",
                "enabled_intervals": [60],
            },
        )
        await db.commit()


def _probing_protect_client(probe: LockProbe, lock_free: list[bool]) -> type:
    """A ProtectClient stand-in whose requests check the write lock while they are in flight."""

    class ProbingProtectClient:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> ProbingProtectClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def historical_snapshot(self, camera_id: str, ts: object) -> bytes:
            lock_free.append(await probe())
            return b"\xff\xd8jpeg"

        async def get_all_camera_recording_ranges(self) -> dict[str, Any]:
            lock_free.append(await probe())
            return {}

    return ProbingProtectClient


async def test_test_protect_connection_calls_the_console_outside_a_transaction(
    client: AsyncClient,
    write_lock_free: LockProbe,
    durable_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_protect_and_a_camera(durable_db)
    await _sign_in(client, durable_db)
    lock_free: list[bool] = []
    monkeypatch.setattr(
        cameras_view_module,
        "ProtectClient",
        _probing_protect_client(write_lock_free, lock_free),
    )

    resp = await client.post(app.url_path_for("cameras.test_protect_connection"))

    assert resp.status_code == 200
    assert "Connected" in resp.text
    assert lock_free == [True]


async def test_historical_panel_asks_protect_outside_a_transaction(
    client: AsyncClient,
    write_lock_free: LockProbe,
    durable_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_protect_and_a_camera(durable_db)
    await _sign_in(client, durable_db)
    lock_free: list[bool] = []
    monkeypatch.setattr(
        timelapses_view_module,
        "ProtectClient",
        _probing_protect_client(write_lock_free, lock_free),
    )

    resp = await client.get(app.url_path_for("timelapses.historical_timelapse_panel"))

    assert resp.status_code == 200
    assert lock_free == [True]


async def test_detect_probes_the_camera_outside_a_transaction(
    client: AsyncClient,
    write_lock_free: LockProbe,
    durable_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_protect_and_a_camera(durable_db)
    await _sign_in(client, durable_db)
    managers: list[_ProbingCameraManager] = []

    class DetectingCameraManager(_ProbingCameraManager):
        def __init__(self, settings: CameraManagerSettings) -> None:
            super().__init__(write_lock_free, [])
            managers.append(self)

    monkeypatch.setattr(cameras_view_module, "CameraManager", DetectingCameraManager)

    resp = await client.post(
        app.url_path_for("cameras.detect_camera_capabilities", camera_id="cam-1")
    )

    assert resp.status_code == 200
    assert [m.lock_free_during_calls for m in managers] == [[True]]
    async with durable_db() as db:
        stored = await camera_crud.get_by_camera_id(db, "cam-1")
        assert stored is not None
        assert stored.rtsp_max_resolution == "3840x2160"


# ── file routes: the row is read, then the file is checked on disk ─────────────────────────────


def _probing_disk(
    probe: LockProbe, lock_free: list[bool]
) -> Callable[..., Awaitable[bool]]:
    """An `async_fs.path_exists` that checks the write lock and reports the file missing."""

    async def path_exists(path: object) -> bool:
        lock_free.append(await probe())
        return False

    return path_exists


async def test_timelapse_file_routes_check_the_disk_outside_a_transaction(
    client: AsyncClient,
    write_lock_free: LockProbe,
    durable_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_protect_and_a_camera(durable_db)
    video = config.VIDEO_OUTPUT_PATH / "2026" / "10" / "yard_1" / "60s" / "yard_1.mp4"
    async with durable_db() as db:
        timelapse = await timelapse_crud.create_from_dict(
            db,
            data={
                "camera_id": "cam-1",
                "camera_safe_name": "yard_1",
                "timelapse_date": date(2026, 10, 30),
                "interval": 60,
                "frame_count": 10,
                "frame_rate": 30,
                "duration_seconds": 1.0,
                "file_path": str(video),
                "file_name": video.name,
                "thumbnail_path": str(video.with_name("yard_1_thumb.jpg")),
                "status": "completed",
                "completed_at": datetime.now(UTC),
            },
        )
        await db.commit()
        timelapse_id = timelapse.id
    await _sign_in(client, durable_db)
    lock_free: list[bool] = []
    monkeypatch.setattr(
        async_fs, "path_exists", _probing_disk(write_lock_free, lock_free)
    )

    for route in (
        "timelapses.serve_timelapse_video",
        "timelapses.serve_timelapse_thumbnail",
    ):
        resp = await client.get(app.url_path_for(route, timelapse_id=timelapse_id))
        assert resp.status_code == 404

    # The video's one check, then the thumbnail's two (its own path, then the video's).
    assert lock_free == [True, True, True]


async def test_capture_file_route_checks_the_disk_outside_a_transaction(
    client: AsyncClient,
    write_lock_free: LockProbe,
    durable_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_protect_and_a_camera(durable_db)
    image = config.IMAGE_OUTPUT_PATH / "yard_1" / "60s" / "2026-10-30" / "yard_1_1.jpg"
    taken = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    async with durable_db() as db:
        camera = await camera_crud.get_by_camera_id(db, "cam-1")
        assert camera is not None
        await capture_crud.create(
            db,
            obj_in=CaptureCreate(
                camera_db_id=camera.id,
                camera_id="cam-1",
                camera_safe_name="yard_1",
                timestamp=int(taken.timestamp()),
                capture_datetime=taken,
                capture_date=date(2026, 10, 30),
                interval=60,
                status=CaptureStatus.SUCCESS,
                capture_method=CaptureMethod.RTSP,
                file_path=str(image),
                file_name=image.name,
            ),
        )
        await db.commit()
    await _sign_in(client, durable_db)
    lock_free: list[bool] = []
    monkeypatch.setattr(
        async_fs, "path_exists", _probing_disk(write_lock_free, lock_free)
    )

    resp = await client.get(
        app.url_path_for(
            "images.get_image_file",
            camera="yard_1",
            interval=60,
            timestamp=int(taken.timestamp()),
        )
    )

    assert resp.status_code == 404
    assert lock_free == [True]
