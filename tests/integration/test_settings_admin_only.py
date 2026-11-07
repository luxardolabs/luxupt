"""The settings that hold the Protect credentials or copy the database are for administrators.

The stored API key and password never reach a page, so saving the form with those fields empty keeps
them, and only the remove toggle clears them.
"""

from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud.user_crud import user_crud
from app.models.camera_model import Camera
from app.models.enum_model import CaptureMethod, HealthStatus
from app.models.fetch_settings_model import FetchSettings
from app.services.core.camera_core_service import CameraCoreService
from app.services.core.capture_core_service import CaptureCoreService
from app.services.core.capture_stats_core_service import CaptureStatsCoreService
from app.services.core.health_core_service import HealthCoreService
from app.services.core.settings_core_service import SettingsCoreService
from app.services.views import timelapses_view_service
from app.services.views.cameras_view_service import CamerasViewService
from app.web.main import app

ADMIN_PASSWORD = "an-admin-long-enough-password"
USER_PASSWORD = "a-user-long-enough-password"
STORED_KEY = "Stored-Protect-Api-Key"
STORED_PASSWORD = "Stored-Protect-Password"
STORED_URL = "https://stored-console.invalid/proxy/protect/integration/v1"
FORM = {
    "default_capture_method": "auto",
    "default_rtsp_quality": "high",
    "rtsp_output_format": "png",
}


async def _sign_in(
    client: AsyncClient, db: AsyncSession, *, is_admin: bool
) -> AsyncClient:
    username, password = (
        ("settings-admin", ADMIN_PASSWORD)
        if is_admin
        else ("settings-user", USER_PASSWORD)
    )
    await user_crud.create_user(
        db, username=username, password=password, is_admin=is_admin
    )
    response = await client.post(
        "/login", data={"username": username, "password": password}
    )
    assert response.status_code in (302, 303)
    return client


async def _store_secrets(db: AsyncSession) -> FetchSettings:
    settings = await db.get(FetchSettings, 1)
    assert settings is not None
    settings.api_key = STORED_KEY
    settings.password = STORED_PASSWORD
    await db.flush()
    return settings


@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("get", "cameras.fetch_settings_panel"),
        ("post", "cameras.save_fetch_settings"),
        ("post", "cameras.test_protect_connection"),
        ("get", "system.backup_settings_panel"),
        ("post", "system.update_backup_settings"),
    ],
)
async def test_a_non_admin_is_refused(
    client: AsyncClient, db: AsyncSession, method: str, route: str
) -> None:
    await _store_secrets(db)
    await _sign_in(client, db, is_admin=False)

    response = await client.request(method, app.url_path_for(route), data=None)

    assert response.status_code == 403
    assert STORED_KEY not in response.text
    assert STORED_PASSWORD not in response.text


async def test_a_non_admin_cannot_repoint_the_console(
    client: AsyncClient, db: AsyncSession
) -> None:
    settings = await _store_secrets(db)
    await _sign_in(client, db, is_admin=False)

    response = await client.post(
        app.url_path_for("cameras.save_fetch_settings"),
        data={**FORM, "base_url": "https://attacker.example", "api_key": "theirs"},
    )

    assert response.status_code == 403
    await db.refresh(settings)
    assert settings.api_key == STORED_KEY
    assert settings.base_url != "https://attacker.example"


async def test_a_non_admin_sees_no_settings_buttons(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _sign_in(client, db, is_admin=False)

    cameras = await client.get(app.url_path_for("cameras.cameras_page"))
    system = await client.get(app.url_path_for("system.system_page"))

    assert cameras.status_code == 200
    assert system.status_code == 200
    assert app.url_path_for("cameras.fetch_settings_panel") not in cameras.text
    assert app.url_path_for("system.backup_settings_panel") not in system.text


async def test_an_admin_sees_the_settings_buttons(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _sign_in(client, db, is_admin=True)

    cameras = await client.get(app.url_path_for("cameras.cameras_page"))
    system = await client.get(app.url_path_for("system.system_page"))

    assert app.url_path_for("cameras.fetch_settings_panel") in cameras.text
    assert app.url_path_for("system.backup_settings_panel") in system.text


async def test_the_panel_never_renders_the_stored_secrets(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _store_secrets(db)
    await _sign_in(client, db, is_admin=True)

    response = await client.get(app.url_path_for("cameras.fetch_settings_panel"))

    assert response.status_code == 200
    assert STORED_KEY not in response.text
    assert STORED_PASSWORD not in response.text
    assert "Remove saved API key" in response.text
    assert "Remove saved password" in response.text


async def test_the_panel_is_not_cached(client: AsyncClient, db: AsyncSession) -> None:
    await _sign_in(client, db, is_admin=True)

    response = await client.get(app.url_path_for("cameras.fetch_settings_panel"))

    assert response.headers["cache-control"] == "no-store"


async def test_the_panel_context_holds_no_secret(db: AsyncSession) -> None:
    await _store_secrets(db)

    context = await CamerasViewService(
        db,
        CameraCoreService(db),
        CaptureCoreService(db),
        CaptureStatsCoreService(db),
        SettingsCoreService(db),
        SimpleNamespace(app=app),
    ).get_fetch_settings_context()

    assert STORED_KEY not in repr(context)
    assert STORED_PASSWORD not in repr(context)
    assert context["has_saved_api_key"] is True
    assert context["has_saved_password"] is True


@pytest.mark.parametrize("route", ["system.system_page", "system.about_page"])
@pytest.mark.parametrize(("is_admin", "shown"), [(False, False), (True, True)])
async def test_only_an_admin_sees_the_base_url(
    client: AsyncClient, db: AsyncSession, route: str, is_admin: bool, shown: bool
) -> None:
    settings = await _store_secrets(db)
    settings.base_url = STORED_URL
    await db.flush()
    await _sign_in(client, db, is_admin=is_admin)

    response = await client.get(app.url_path_for(route))

    assert response.status_code == 200
    assert (STORED_URL in response.text) is shown


class _RefusingProtect:
    """A console whose refusal repeats what it was sent."""

    def __init__(self, *_: object, **__: object) -> None:
        pass

    async def __aenter__(self) -> _RefusingProtect:
        raise ConnectionError(f"login failed: {STORED_PASSWORD}")

    async def __aexit__(self, *_: object) -> None:
        return None


async def test_a_failed_console_call_shows_no_detail_on_the_historical_page(
    client: AsyncClient,
    durable_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(timelapses_view_service, "ProtectClient", _RefusingProtect)
    async with durable_db() as session:
        settings = await _store_secrets(session)
        settings.base_url = STORED_URL
        settings.username = "stored-user"
        session.add(
            Camera(
                camera_id="historical-cam",
                name="Historical Cam",
                safe_name="historical_cam",
                is_active=True,
                is_connected=True,
                capture_method=CaptureMethod.AUTO,
                rtsp_quality="high",
            )
        )
        await user_crud.create_user(
            session, username="settings-admin", password=ADMIN_PASSWORD
        )
        await session.commit()
    signed_in = await client.post(
        "/login", data={"username": "settings-admin", "password": ADMIN_PASSWORD}
    )
    assert signed_in.status_code in (302, 303)

    response = await client.get("/timelapses/historical")

    assert response.status_code == 200
    assert "Could not read recording ranges" in response.text
    assert STORED_PASSWORD not in response.text


class _RefusedCameraManager:
    async def get_cameras(self) -> list[object]:
        raise ConnectionError(f"401 Unauthorized for url '{STORED_URL}/cameras'")


async def test_the_unauthenticated_health_check_does_not_name_the_console() -> None:
    report = await HealthCoreService(_RefusedCameraManager()).check_camera_manager()

    assert report["status"] == HealthStatus.UNHEALTHY
    assert report["message"] == "Camera manager error"


async def _save_and_read_back(
    client: AsyncClient,
    durable_db: async_sessionmaker[AsyncSession],
    **form: str,
) -> tuple[str | None, str | None]:
    """Sign an admin in with secrets stored, save the form, and read the secrets back committed."""
    async with durable_db() as session:
        await _store_secrets(session)
        await user_crud.create_user(
            session, username="settings-admin", password=ADMIN_PASSWORD
        )
        await session.commit()
    signed_in = await client.post(
        "/login", data={"username": "settings-admin", "password": ADMIN_PASSWORD}
    )
    assert signed_in.status_code in (302, 303)

    response = await client.post(
        app.url_path_for("cameras.save_fetch_settings"), data={**FORM, **form}
    )
    assert "Settings saved" in response.text, " ".join(response.text.split())[-400:]

    async with durable_db() as session:
        settings = await session.get(FetchSettings, 1)
    assert settings is not None
    return settings.api_key, settings.password


async def test_saving_with_empty_secret_fields_keeps_them(
    client: AsyncClient, durable_db: async_sessionmaker[AsyncSession]
) -> None:
    stored = await _save_and_read_back(client, durable_db, api_key="", password="")

    assert stored == (STORED_KEY, STORED_PASSWORD)


async def test_a_typed_value_replaces_a_secret_even_with_the_toggle_on(
    client: AsyncClient, durable_db: async_sessionmaker[AsyncSession]
) -> None:
    stored = await _save_and_read_back(
        client, durable_db, api_key="a-new-key", remove_api_key="true"
    )

    assert stored == ("a-new-key", STORED_PASSWORD)


async def test_the_remove_toggles_clear_the_secrets(
    client: AsyncClient, durable_db: async_sessionmaker[AsyncSession]
) -> None:
    stored = await _save_and_read_back(
        client, durable_db, remove_api_key="true", remove_password="true"
    )

    assert stored == (None, None)
