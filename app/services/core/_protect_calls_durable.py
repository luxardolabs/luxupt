"""What a request needs around a slow call, read and written in OWNED short transactions.

The app begins every transaction IMMEDIATE, so a session that has run any statement holds the
database's one write lock until it commits. A request that read its inputs on its own session and
then called the console or a camera, or checked a file on a mounted disk, would hold that lock for
the whole call, stalling every page and the fetch worker. The request paths that make such a call
read here instead: Test Protect connection, camera Detect and the historical panel (a Protect or
camera request), and the timelapse thumbnail, timelapse video and capture file routes (a disk
check). Each function opens a session, reads or writes, and commits before it returns, so the
call itself runs with no transaction open and the request's own session runs nothing before it.

Private to this package; callers reach it through ``SettingsCoreService`` and
``CameraCoreService``. Declared in ``[allowlist].non_request_files`` as a transaction owner.
"""

from app.clients.camera_manager import CameraManagerSettings
from app.core import config
from app.crud import capture_crud, timelapse_crud
from app.crud.camera_crud import camera_crud
from app.crud.fetch_settings_crud import fetch_settings_crud
from app.crud.scheduler_settings_crud import scheduler_settings_crud
from app.db.database import get_db_context
from app.models.fetch_settings_model import FetchSettings
from app.schemas.camera_schema import CameraRead
from app.schemas.capture_schema import CaptureRead
from app.schemas.scheduler_settings_schema import SchedulerSettingsRead
from app.schemas.timelapse_schema import TimelapseRead


def protect_credentials(s: FetchSettings) -> tuple[str, str, str, bool]:
    """(base_url, username, password, verify_ssl) with env-var precedence over the database."""
    base_url = config.UNIFI_PROTECT_BASE_URL or s.base_url or ""
    username = config.UNIFI_PROTECT_USERNAME or s.username or ""
    password = config.UNIFI_PROTECT_PASSWORD or s.password or ""
    verify_ssl = (
        config.UNIFI_PROTECT_VERIFY_SSL
        if config.UNIFI_PROTECT_BASE_URL
        else bool(s.verify_ssl)
    )
    return base_url, username, password, verify_ssl


def camera_manager_settings(s: FetchSettings) -> CameraManagerSettings:
    """CameraManager settings with env-var precedence over the database."""
    return CameraManagerSettings(
        base_url=config.UNIFI_PROTECT_BASE_URL or s.base_url or "",
        api_key=config.UNIFI_PROTECT_API_KEY or s.api_key or "",
        verify_ssl=config.UNIFI_PROTECT_VERIFY_SSL
        if config.UNIFI_PROTECT_BASE_URL
        else s.verify_ssl,
        request_timeout=s.request_timeout,
        rate_limit=s.rate_limit,
        rate_limit_buffer=s.rate_limit_buffer,
        min_offset_seconds=s.min_offset_seconds,
        max_offset_seconds=s.max_offset_seconds,
        camera_refresh_interval=s.camera_refresh_interval,
    )


async def read_protect_credentials() -> tuple[str, str, str, bool]:
    """The Protect credentials in effect, read in an owned transaction."""
    async with get_db_context() as db:
        return protect_credentials(await fetch_settings_crud.get_settings(db))


async def read_camera_manager_settings() -> CameraManagerSettings:
    """The CameraManager settings in effect, read in an owned transaction."""
    async with get_db_context() as db:
        return camera_manager_settings(await fetch_settings_crud.get_settings(db))


async def read_scheduler_settings() -> SchedulerSettingsRead:
    """The scheduler settings, read in an owned transaction."""
    async with get_db_context() as db:
        return SchedulerSettingsRead.model_validate(
            await scheduler_settings_crud.get_settings(db)
        )


async def read_active_cameras() -> list[CameraRead]:
    """The active cameras, read in an owned transaction."""
    async with get_db_context() as db:
        return [CameraRead.model_validate(c) for c in await camera_crud.get_active(db)]


async def read_camera(camera_id: str) -> CameraRead | None:
    """One camera by its UniFi camera ID, read in an owned transaction."""
    async with get_db_context() as db:
        camera = await camera_crud.get_by_camera_id(db, camera_id)
        return CameraRead.model_validate(camera) if camera else None


async def read_camera_by_safe_name(safe_name: str) -> CameraRead | None:
    """One camera by its safe name, read in an owned transaction."""
    async with get_db_context() as db:
        camera = await camera_crud.get_by_safe_name(db, safe_name)
        return CameraRead.model_validate(camera) if camera else None


async def read_capture(
    camera_id: str, timestamp: int, interval: int | None
) -> CaptureRead | None:
    """One capture by camera and timestamp, read in an owned transaction."""
    async with get_db_context() as db:
        capture = await capture_crud.get_by_camera_and_timestamp(
            db, camera_id, timestamp, interval
        )
        return CaptureRead.model_validate(capture) if capture else None


async def read_timelapse(timelapse_id: int) -> TimelapseRead | None:
    """One timelapse by id, read in an owned transaction."""
    async with get_db_context() as db:
        timelapse = await timelapse_crud.get(db, timelapse_id)
        return TimelapseRead.model_validate(timelapse) if timelapse else None


async def write_capability_detection(
    camera_id: str,
    *,
    api_max_resolution: str | None,
    rtsp_max_resolution: str | None,
    recommended_method: str | None,
) -> CameraRead | None:
    """Store a camera's capability-detection result in an owned, committed transaction."""
    async with get_db_context() as db:
        camera = await camera_crud.update_capability_detection(
            db,
            camera_id,
            api_max_resolution=api_max_resolution,
            rtsp_max_resolution=rtsp_max_resolution,
            recommended_method=recommended_method,
        )
        return CameraRead.model_validate(camera) if camera else None
