"""A non-admin views: only an admin changes anything, and only an admin sees the controls to.

The route walk reads every registered route, so a new state-changing route is held to this without
anyone remembering to add it here.
"""

from collections.abc import Iterable, Iterator
from datetime import date

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import job_crud
from app.crud.user_crud import user_crud
from app.models.camera_model import Camera
from app.models.enum_model import CaptureMethod, TimelapseStatus
from app.models.timelapse_model import Timelapse
from app.web.main import app

PASSWORD = "a-long-enough-password"
# Signing in, first-run setup and a user's own password are not admin actions.
OPEN_TO_ANY_USER = {
    "pages.login_submit",
    "admin.setup",
    "account.change_password_submit",
}
ADMIN_ONLY_PANELS = {
    "cameras.fetch_settings_panel",
    "cameras.camera_settings_panel",
    "images.delete_images_panel",
    "images.delete_images_preview",
    "system.backup_settings_panel",
    "timelapse_scheduler.scheduler_panel",
    "timelapses.create_timelapse_panel",
    "timelapses.historical_timelapse_panel",
}


def _routes(routes: Iterable[object] = app.routes) -> Iterator[APIRoute]:
    # An included router sits in app.routes as one entry that holds its routes.
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _routes(included.routes)


def _url(route: APIRoute) -> str:
    params = dict.fromkeys(route.param_convertors, "1")
    return str(app.url_path_for(route.name, **params))


STATE_CHANGING = sorted(
    (method, route.name)
    for route in _routes()
    for method in route.methods - {"GET", "HEAD"}
    if route.name not in OPEN_TO_ANY_USER
)
BY_NAME = {route.name: route for route in _routes()}


async def _sign_in(
    client: AsyncClient, db: AsyncSession, *, is_admin: bool
) -> AsyncClient:
    await user_crud.create_user(
        db, username="signed-in", password=PASSWORD, is_admin=is_admin
    )
    response = await client.post(
        "/login", data={"username": "signed-in", "password": PASSWORD}
    )
    assert response.status_code in (302, 303)
    return client


def test_the_route_walk_sees_the_routes_it_guards() -> None:
    assert len(STATE_CHANGING) >= 15
    assert len(BY_NAME) >= 60
    assert ("DELETE", "images.delete_images") in STATE_CHANGING
    assert set(BY_NAME) >= ADMIN_ONLY_PANELS


@pytest.mark.parametrize(("method", "name"), STATE_CHANGING)
async def test_a_non_admin_cannot_call_a_state_changing_route(
    client: AsyncClient, db: AsyncSession, method: str, name: str
) -> None:
    await _sign_in(client, db, is_admin=False)

    response = await client.request(method, _url(BY_NAME[name]))

    assert response.status_code == 403


@pytest.mark.parametrize("name", sorted(ADMIN_ONLY_PANELS))
async def test_a_non_admin_cannot_open_a_panel_that_leads_to_a_change(
    client: AsyncClient, db: AsyncSession, name: str
) -> None:
    await _sign_in(client, db, is_admin=False)

    response = await client.get(_url(BY_NAME[name]))

    assert response.status_code == 403


async def test_a_non_admin_can_still_change_their_own_password(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _sign_in(client, db, is_admin=False)

    response = await client.post(
        "/account/password",
        data={
            "current_password": PASSWORD,
            "new_password": "a-new-long-enough-password",
            "confirm_password": "a-new-long-enough-password",
        },
    )

    assert response.status_code == 303


async def test_a_non_admin_is_offered_their_own_password_form_and_no_users_tab(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _sign_in(client, db, is_admin=False)

    page = await client.get("/system")

    assert "/account/password" in page.text
    assert "/system/users" not in page.text


async def test_a_voluntary_password_change_can_be_cancelled(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _sign_in(client, db, is_admin=False)

    page = await client.get("/account/password")

    assert ">Cancel</a>" in page.text


async def test_a_forced_password_change_cannot_be_cancelled(
    client: AsyncClient, db: AsyncSession
) -> None:
    await user_crud.create_user(
        db, username="forced", password=PASSWORD, must_change_password=True
    )
    await client.post("/login", data={"username": "forced", "password": PASSWORD})

    page = await client.get("/account/password")

    assert page.status_code == 200
    assert ">Cancel</a>" not in page.text


async def test_an_admin_is_offered_the_users_tab(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _sign_in(client, db, is_admin=True)

    page = await client.get("/system")

    assert "/system/users" in page.text


@pytest.mark.parametrize("name", ["", "Lobby #2", "Who? Door", "G4 Bullet", ".."])
async def test_a_camera_with_an_awkward_name_opens_from_its_card(
    client: AsyncClient, db: AsyncSession, name: str
) -> None:
    for camera_id, safe_name in (("cam-a", name), ("cam-b", name)):
        db.add(
            Camera(
                camera_id=camera_id,
                name=name,
                safe_name=safe_name,
                is_active=True,
                is_connected=True,
                capture_method=CaptureMethod.AUTO,
                rtsp_quality="high",
            )
        )
    await db.flush()
    await _sign_in(client, db, is_admin=False)

    page = await client.get("/cameras")
    panel = await client.get("/cameras/cam-a/panel")

    assert page.status_code == 200
    assert "/cameras/cam-a/panel" in page.text
    assert panel.status_code == 200


@pytest.fixture
async def one_of_everything(db: AsyncSession) -> dict[str, str]:
    db.add(
        Camera(
            camera_id="read-only-cam",
            name="Read Only Cam",
            safe_name="read_only_cam",
            is_active=True,
            is_connected=True,
            capture_method=CaptureMethod.AUTO,
            rtsp_quality="high",
        )
    )
    timelapse = Timelapse(
        camera_id="read-only-cam",
        camera_safe_name="read_only_cam",
        timelapse_date=date(2026, 1, 1),
        interval=60,
        frame_count=100,
        frame_rate=30,
        duration_seconds=3.3,
        status=TimelapseStatus.COMPLETED,
    )
    db.add(timelapse)
    await db.flush()

    async def job(title: str) -> str:
        created = await job_crud.create_job(
            db,
            title=title,
            camera_safe_name="read_only_cam",
            target_date=date(2026, 10, 1),
            interval=60,
        )
        return created.job_id

    running = await job("running")
    await job_crud.start_job(db, running)
    pending = await job("pending")
    done = await job("done")
    await job_crud.start_job(db, done)
    await job_crud.complete_job(db, done, output_file="x.mp4")
    return {
        "timelapse": str(timelapse.id),
        "running": running,
        "pending": pending,
        "done": done,
    }


@pytest.mark.parametrize(("is_admin", "shown"), [(False, False), (True, True)])
async def test_only_an_admin_sees_the_controls(
    client: AsyncClient,
    db: AsyncSession,
    one_of_everything: dict[str, str],
    is_admin: bool,
    shown: bool,
) -> None:
    await _sign_in(client, db, is_admin=is_admin)
    pages = {
        path: (await client.get(path))
        for path in (
            "/cameras",
            "/cameras/partials/list",
            "/cameras/partials/card/read_only_cam",
            "/cameras/read-only-cam/panel",
            "/images",
            "/timelapses",
            "/timelapses/jobs",
            "/timelapses/partials/jobs",
            "/timelapses/partials/completed",
            "/timelapses/partials/list",
            f"/timelapses/partials/job/{one_of_everything['running']}",
            f"/timelapses/partials/job/{one_of_everything['pending']}",
        )
    }
    assert {r.status_code for r in pages.values()} == {200}, {
        p: r.status_code for p, r in pages.items()
    }

    def has(path: str, control: str) -> bool:
        return control in pages[path].text

    settings = "/cameras/read-only-cam/settings"
    delete_timelapse = f'/timelapses/{one_of_everything["timelapse"]}"'
    assert has("/cameras", settings) is shown
    assert has("/cameras", "/cameras/read-only-cam/panel") is not shown
    assert has("/cameras/partials/list", settings) is shown
    assert has("/cameras/partials/card/read_only_cam", settings) is shown
    assert has("/cameras/read-only-cam/panel", settings) is shown
    assert has("/images", "/images/delete") is shown
    assert has("/timelapses", "/timelapses/create") is shown
    assert has("/timelapses", "/timelapses/historical") is shown
    assert has("/timelapses", "/timelapses/scheduler") is shown
    assert has("/timelapses/jobs", "/timelapses/create") is shown
    assert has("/timelapses", delete_timelapse) is shown
    assert has("/timelapses/partials/list", delete_timelapse) is shown
    for job in ("running", "pending"):
        job_path = f'/timelapses/job/{one_of_everything[job]}"'
        assert has("/timelapses/jobs", job_path) is shown
        assert has("/timelapses/partials/jobs", job_path) is shown
        assert (
            has(f"/timelapses/partials/job/{one_of_everything[job]}", job_path) is shown
        )
    assert (
        has(
            "/timelapses/partials/completed",
            f'/timelapses/job/{one_of_everything["done"]}"',
        )
        is shown
    )
