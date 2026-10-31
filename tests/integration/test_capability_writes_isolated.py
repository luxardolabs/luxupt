"""One camera's failed capability write never costs the other cameras theirs.

Discovery writes every new camera's detected capabilities in one transaction. A write that fails
(here a flush error) is undone alone, and the session stays usable for the cameras after it, so
their results are still stored when the transaction commits.
"""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.web.main as web_main
import app.workers.fetch_service as fetch_service_module
from app.clients.camera_manager import Camera, CameraManager, CameraManagerSettings
from app.crud.camera_crud import camera_crud
from app.models.camera_model import Camera as CameraRow
from app.web.main import sync_cameras_to_db
from app.workers.fetch_service import FetchService

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


class _TwoNewCameras(CameraManager):
    """Lists two new connected cameras and detects the same capabilities for each."""

    def __init__(self) -> None:
        super().__init__(_SETTINGS)
        self.listed = [
            Camera(
                id=f"cam-{n}",
                name=f"Yard {n}",
                state="CONNECTED",
                mac="",
                is_connected=True,
                is_recording=True,
            )
            for n in (1, 2)
        ]

    async def get_cameras(self, force_refresh: bool = False) -> list[Camera]:
        return self.listed

    async def detect_camera_capabilities(self, camera: Camera) -> dict[str, Any]:
        return {"rtsp_max_resolution": "3840x2160", "recommended_method": "rtsp"}


def _first_camera_write_fails(module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    store = module.camera_crud.update_capability_detection

    async def failing_for_cam_1(db: AsyncSession, camera_id: str, **kw: Any) -> Any:
        if camera_id == "cam-1":
            db.add(CameraRow())  # every NOT NULL column missing: the flush fails
            await db.flush()
        return await store(db, camera_id, **kw)

    monkeypatch.setattr(
        module.camera_crud, "update_capability_detection", failing_for_cam_1
    )


async def _second_camera_resolution(
    durable_db: async_sessionmaker[AsyncSession],
) -> str | None:
    async with durable_db() as db:
        stored = await camera_crud.get_by_camera_id(db, "cam-2")
        assert stored is not None
        return stored.rtsp_max_resolution


async def test_startup_sync_keeps_the_other_cameras_capabilities(
    durable_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _first_camera_write_fails(web_main, monkeypatch)

    await sync_cameras_to_db(_TwoNewCameras())

    assert await _second_camera_resolution(durable_db) == "3840x2160"


async def test_fetch_worker_sync_keeps_the_other_cameras_capabilities(
    durable_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _first_camera_write_fails(fetch_service_module, monkeypatch)
    service = FetchService()
    service.camera_manager = _TwoNewCameras()

    await service._sync_cameras_to_db()

    assert await _second_camera_resolution(durable_db) == "3840x2160"
