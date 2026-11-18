"""The timelapse create form takes a real date and a positive interval, and names the job canonically.

Every ISO spelling of a day (`20261117`, `2026-W47-2`) is that day, so the job is named by its
`YYYY-MM-DD` date and a second spelling is the same job. An interval below one second, or a value
that is no date, is refused with a 422 and creates nothing.
"""

import types
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.views.timelapses_view_service as timelapses_view
from app.crud.user_crud import user_crud
from app.models.camera_model import Camera
from app.models.job_model import Job
from app.web.main import app

PASSWORD = "a-long-enough-password"


@pytest_asyncio.fixture
async def admin(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(
        timelapses_view,
        "get_job_processor",
        lambda: types.SimpleNamespace(start_job=lambda *a, **k: None),
    )
    db.add(
        Camera(
            camera_id="cam-1",
            name="Front Door",
            safe_name="front_door",
            video_mode="default",
            supports_full_hd_snapshot=False,
            state="CONNECTED",
        )
    )
    await user_crud.create_user(
        db, username="create-admin", password=PASSWORD, is_admin=True
    )
    await db.flush()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        resp = await ac.post(
            "/login", data={"username": "create-admin", "password": PASSWORD}
        )
        assert resp.status_code in (302, 303)
        yield ac


async def _create(admin: AsyncClient, date_value: str, interval: str) -> int:
    resp = await admin.post(
        "/timelapses/create",
        data={"camera_id": "cam-1", "date": date_value, "interval": interval},
        headers={"HX-Request": "true"},
    )
    return resp.status_code


async def _jobs(db: AsyncSession) -> list[tuple[str, str, int]]:
    rows = (await db.scalars(select(Job))).all()
    return [(j.title, j.target_date.isoformat(), j.interval) for j in rows]


async def test_a_canonical_date_creates_one_job(
    admin: AsyncClient, db: AsyncSession
) -> None:
    assert await _create(admin, "2026-11-17", "60") == 200
    assert await _jobs(db) == [("front_door_2026-11-17_60s", "2026-11-17", 60)]


@pytest.mark.parametrize("spelling", ["20261117", "2026-W47-2", "2026W472"])
async def test_another_iso_spelling_names_the_job_by_its_day(
    admin: AsyncClient, db: AsyncSession, spelling: str
) -> None:
    assert await _create(admin, spelling, "60") == 200
    assert await _jobs(db) == [("front_door_2026-11-17_60s", "2026-11-17", 60)]


async def test_a_second_spelling_of_the_same_day_is_the_same_job(
    admin: AsyncClient, db: AsyncSession
) -> None:
    await _create(admin, "2026-11-17", "60")
    await _create(admin, "20261117", "60")
    assert len(await _jobs(db)) == 1


@pytest.mark.parametrize("interval", ["0", "-60"])
async def test_an_interval_below_one_second_is_refused(
    admin: AsyncClient, db: AsyncSession, interval: str
) -> None:
    assert await _create(admin, "2026-11-17", interval) == 422
    assert await _jobs(db) == []


@pytest.mark.parametrize("date_value", ["2026-13-01", "", "yesterday", "2026-02-30"])
async def test_a_value_that_is_no_date_is_refused(
    admin: AsyncClient, db: AsyncSession, date_value: str
) -> None:
    assert await _create(admin, date_value, "60") == 422
    assert await _jobs(db) == []
