"""A query value the route cannot use is refused at the boundary, never a 500.

`date` must be an ISO date and `interval` an integer. A value that is neither is a 422 that names
the field: a 500 would log a traceback that quotes the value. An empty value (the dropdown's "All")
is no filter. A camera card for a camera that does not exist is the not-found partial.
"""

import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import JsonFormatter
from app.crud.user_crud import user_crud
from app.web.main import app

SENT = "hunter2-not-a-date-or-number"
PASSWORD = "a-long-enough-password"

BOTH = {"date", "interval"}
# Each route, with the query parameters it declares.
ROUTES = {
    "/images": BOTH,
    "/images/partials/grid": BOTH,
    "/images/lightbox/somecam/20260101T000000": BOTH,
    "/images/lightbox/somecam/20260101T000000/content": BOTH,
    "/images/delete/preview": BOTH,
    "/timelapses": BOTH,
    "/timelapses/partials/intervals": {"date"},
    "/timelapses/partials/list": BOTH,
    "/timelapses/partials/preview": BOTH,
}


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    """A signed-in admin that receives the app's 500 instead of the re-raised error."""
    await user_crud.create_user(
        db, username="garbage-admin", password=PASSWORD, is_admin=True
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        resp = await ac.post(
            "/login", data={"username": "garbage-admin", "password": PASSWORD}
        )
        assert resp.status_code in (302, 303)
        yield ac


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("query", [f"date={SENT}", f"interval={SENT}"])
async def test_a_garbage_date_or_interval_is_a_422_that_never_echoes_it(
    admin: AsyncClient, caplog: pytest.LogCaptureFixture, path: str, query: str
) -> None:
    with caplog.at_level(logging.INFO):
        resp = await admin.get(f"{path}?{query}")

    if query.split("=")[0] not in ROUTES[path]:
        assert resp.status_code < 500
        return
    assert resp.status_code == 422
    assert SENT not in resp.text
    fmt = JsonFormatter(service="luxupt", version="test")
    assert [r for r in caplog.records if SENT in fmt.format(r)] == []


@pytest.mark.parametrize("path", ROUTES)
async def test_a_real_date_and_interval_are_no_500(
    admin: AsyncClient, path: str
) -> None:
    resp = await admin.get(f"{path}?date=2026-11-17&interval=60")
    assert resp.status_code < 500


@pytest.mark.parametrize("path", ROUTES)
async def test_an_empty_date_and_interval_are_no_filter(
    admin: AsyncClient, path: str
) -> None:
    resp = await admin.get(f"{path}?date=&interval=")
    assert resp.status_code < 500


TOO_BIG = str(2**64)


@pytest.mark.parametrize(
    "path", [p for p, params in ROUTES.items() if "interval" in params]
)
async def test_an_interval_too_large_to_store_is_a_422(
    admin: AsyncClient, path: str
) -> None:
    resp = await admin.get(f"{path}?interval={TOO_BIG}")
    assert resp.status_code == 422


@pytest.mark.parametrize("path", ["/images", "/timelapses", "/system/activity"])
async def test_a_page_too_large_to_offset_is_a_422(
    admin: AsyncClient, path: str
) -> None:
    resp = await admin.get(f"{path}?page={TOO_BIG}")
    assert resp.status_code == 422


@pytest.mark.parametrize("path", ["/images", "/timelapses", "/system/activity"])
async def test_the_last_allowed_page_is_no_500(admin: AsyncClient, path: str) -> None:
    resp = await admin.get(f"{path}?page=1000000")
    assert resp.status_code < 500


async def test_an_unknown_camera_card_is_the_not_found_partial(
    admin: AsyncClient,
) -> None:
    resp = await admin.get("/cameras/partials/card/no-such-camera")
    assert resp.status_code == 404


@pytest.mark.parametrize(
    ("interval", "date_value"),
    [
        ("hunter2-not-a-number", "2026-11-17"),
        ("60-hunter2-seconds", "2026-11-17"),
        ("60", "hunter2-not-a-date"),
    ],
)
async def test_a_garbage_create_form_value_is_a_422_that_never_echoes_it(
    admin: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    interval: str,
    date_value: str,
) -> None:
    with caplog.at_level(logging.INFO):
        resp = await admin.post(
            "/timelapses/create",
            data={
                "camera_id": "no-such-camera",
                "date": date_value,
                "interval": interval,
            },
            headers={"HX-Request": "true"},
        )

    assert resp.status_code == 422
    sent = interval if interval != "60" else date_value
    assert sent not in resp.text
    fmt = JsonFormatter(service="luxupt", version="test")
    assert [r for r in caplog.records if sent in fmt.format(r)] == []


async def test_a_real_create_form_is_no_500(admin: AsyncClient) -> None:
    resp = await admin.post(
        "/timelapses/create",
        data={"camera_id": "no-such-camera", "date": "2026-11-17", "interval": "60"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code < 500
