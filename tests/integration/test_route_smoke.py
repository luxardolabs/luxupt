"""Route smoke — every registered route is REQUESTED by the suite at least once.

The point is not what these routes return. It is that they RUN, so that the canonical
`filterwarnings = error` actually fires on them: a dependency deprecation is invisible to
luxaudit (not a CVE), to mypy/ruff (the attribute still exists, the behaviour changed) and to
the lock diff — and `filterwarnings = error` is inert on any line no test executes. Pairing
the two is what catches it a full version before the pin that makes it fatal.

So the assertion is deliberately `< 500`: a 200, a 302, a 404 for a row that isn't there, a
422 for a query this test didn't bother to satisfy — all mean the handler ran and returned.
A 500 means it raised, which is the thing worth knowing. Asserting a response SHAPE here
would be wrong: several of these are fragment endpoints that legitimately return bare HTML,
and a shape assertion would make them look broken (see --playbook route-smoke).

Login goes through the REAL /login route via the `auth_client` fixture — never a forged
cookie or an overridden auth dependency, which would test a path that doesn't ship.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.db.connection import async_session
from app.models.camera_model import Camera
from app.models.capture_model import Capture
from app.models.enum_model import CaptureMethod, CaptureStatus, JobStatus
from app.models.job_model import Job
from app.models.timelapse_model import Timelapse
from app.models.user_model import User
from app.utils.timezones import business_day

CAMERA_ID = "route-smoke-camera-uuid"
CAMERA_SAFE_NAME = "Route_Smoke_Cam"
JOB_ID = "route-smoke-job"
TIMESTAMP = 1767225600  # 2026-01-01 00:00:00 UTC


@pytest_asyncio.fixture
async def seeded() -> AsyncGenerator[dict[str, Any]]:
    """One row of each kind the path-parameter routes need, committed so the app sees them.

    The routes under `{camera_id}` / `{job_id}` / `{timelapse_id}` would 404 on an empty
    database, which still proves the route ran — but a 404 short-circuits before most of the
    handler, so the render path (the part a template or dependency change breaks) would go
    unexercised. Real rows make the smoke reach the code that matters.
    """
    now = datetime.now(UTC)
    today = business_day()
    async with async_session() as db:
        camera = Camera(
            camera_id=CAMERA_ID,
            name="Route Smoke Cam",
            safe_name=CAMERA_SAFE_NAME,
            is_active=True,
            is_connected=True,
            capture_method=CaptureMethod.AUTO,
            rtsp_quality="high",
            enabled_intervals=[60],
            first_discovered_at=now,
        )
        db.add(camera)
        db.add(
            Capture(
                camera_id=CAMERA_ID,
                camera_safe_name=CAMERA_SAFE_NAME,
                timestamp=TIMESTAMP,
                capture_datetime=now,
                capture_date=today,
                interval=60,
                status=CaptureStatus.SUCCESS,
                capture_method=CaptureMethod.API,
                file_path=f"/nonexistent/{CAMERA_SAFE_NAME}/{TIMESTAMP}.jpg",
                file_size=1024,
            )
        )
        db.add(
            Job(
                job_id=JOB_ID,
                title=f"{CAMERA_SAFE_NAME}_{today}_60s",
                camera_safe_name=CAMERA_SAFE_NAME,
                target_date=today,
                status=JobStatus.PENDING,
                interval=60,
                created_at=now,
            )
        )
        timelapse = Timelapse(
            camera_id=CAMERA_ID,
            camera_safe_name=CAMERA_SAFE_NAME,
            timelapse_date=today,
            interval=60,
            frame_count=10,
            frame_rate=30,
            duration_seconds=0.33,
            file_path=f"/nonexistent/{CAMERA_SAFE_NAME}.mp4",
        )
        db.add(timelapse)
        user = User(username="route-smoke-user", password_hash="x", is_admin=False)
        db.add(user)
        await db.commit()
        await db.refresh(timelapse)
        await db.refresh(user)
        ids = {"timelapse_id": timelapse.id, "user_id": user.id}

    yield ids

    # The suite shares one database across tests; clean up what this fixture committed.
    async with async_session() as db:
        for model, column, value in (
            (Capture, Capture.camera_id, CAMERA_ID),
            (Job, Job.job_id, JOB_ID),
            (Timelapse, Timelapse.camera_id, CAMERA_ID),
            (Camera, Camera.camera_id, CAMERA_ID),
            (User, User.username, "route-smoke-user"),
        ):
            for row in (
                await db.execute(select(model).where(column == value))
            ).scalars():
                await db.delete(row)
        await db.commit()


def _ran(resp: Any, what: str) -> None:
    """Assert the handler ran and returned rather than raising."""
    assert resp.status_code < 500, f"{what} -> {resp.status_code}\n{resp.text[:400]}"


# ── GET: no path parameters ────────────────────────────────────────────────────────────

STATIC_GETS = [
    "/dashboard",
    "/openapi.json",
    "/cameras/capture-stats",
    "/cameras/capture-stats/charts",
    "/cameras/fetch-settings",
    "/cameras/partials/list",
    "/images/partials/grid",
    "/images/partials/filters",
    "/images/delete",
    "/images/delete/preview",
    "/system/about",
    "/system/activity",
    "/system/users",
    "/system/partials/activity",
    "/system/partials/backup-settings",
    "/system/partials/user-form",
    "/system/partials/users",
    "/timelapses/create",
    "/timelapses/historical",
    "/timelapses/partials/completed",
    "/timelapses/partials/dates",
    "/timelapses/partials/intervals",
    "/timelapses/partials/jobs",
    "/timelapses/partials/list",
    "/timelapses/partials/preview",
    "/timelapses/partials/stats",
]


class TestAuthenticatedGets:
    """Every authenticated GET with no path parameter."""

    @pytest.mark.parametrize("path", STATIC_GETS)
    async def test_get_runs(self, auth_client: AsyncClient, path: str) -> None:
        _ran(await auth_client.get(path), f"GET {path}")


class TestPublicGets:
    """GETs reachable without a session."""

    async def test_setup_page(self, client: AsyncClient) -> None:
        _ran(await client.get("/setup"), "GET /setup")

    async def test_logout(self, auth_client: AsyncClient) -> None:
        # Uses its own authed client: logout clears the session cookie, and the fixture is
        # function-scoped, so it cannot leak into another test.
        _ran(await auth_client.get("/logout"), "GET /logout")


# ── GET: path parameters, against seeded rows ──────────────────────────────────────────


class TestParameterisedGets:
    """Routes that carry a path parameter, exercised against real rows."""

    async def test_camera_routes(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        for path in (
            f"/cameras/{CAMERA_SAFE_NAME}",
            f"/cameras/{CAMERA_SAFE_NAME}/panel",
            f"/cameras/partials/card/{CAMERA_SAFE_NAME}",
            f"/cameras/{CAMERA_ID}/settings",
        ):
            _ran(await auth_client.get(path), f"GET {path}")

    async def test_image_routes(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        today = business_day().isoformat()
        for path in (
            f"/images/lightbox/{CAMERA_SAFE_NAME}/{TIMESTAMP}",
            f"/images/lightbox/{CAMERA_SAFE_NAME}/{TIMESTAMP}/content",
            # The file/thumbnail routes serve from disk; the seeded capture points at a
            # path that does not exist, so these 404 from the handler — which is the
            # handler running, not failing.
            f"/images/file/{CAMERA_SAFE_NAME}/60/{TIMESTAMP}",
            f"/images/thumbnail/{CAMERA_SAFE_NAME}/60/{today}/{TIMESTAMP}",
        ):
            _ran(await auth_client.get(path), f"GET {path}")

    async def test_job_and_timelapse_routes(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        tl = seeded["timelapse_id"]
        for path in (
            f"/timelapses/partials/job/{JOB_ID}",
            f"/timelapses/{tl}/lightbox",
            f"/timelapses/{tl}/thumbnail",
            f"/timelapses/{tl}/video",
        ):
            _ran(await auth_client.get(path), f"GET {path}")

    async def test_user_delete_confirm(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        path = f"/system/partials/user-delete-confirm/{seeded['user_id']}"
        _ran(await auth_client.get(path), f"GET {path}")


# ── Mutations ──────────────────────────────────────────────────────────────────────────


class TestSettingsPosts:
    """Settings forms — these write, which is why the suite runs on a throwaway database."""

    async def test_save_fetch_settings(self, auth_client: AsyncClient) -> None:
        resp = await auth_client.post(
            "/cameras/fetch-settings",
            data={
                "enabled": "on",
                "intervals": [60],
                "default_capture_method": "auto",
                "default_rtsp_quality": "high",
            },
        )
        _ran(resp, "POST /cameras/fetch-settings")

    async def test_save_scheduler_settings(self, auth_client: AsyncClient) -> None:
        resp = await auth_client.post(
            "/timelapses/scheduler",
            data={
                "run_time": "01:00",
                "days_ago": 1,
                "source": "captured",
                "concurrent_jobs": 2,
            },
        )
        _ran(resp, "POST /timelapses/scheduler")

    async def test_update_backup_settings(self, auth_client: AsyncClient) -> None:
        resp = await auth_client.post(
            "/system/backup-settings",
            data={
                "enabled": "false",
                "retention": 5,
                "interval_hours": 1,
                "backup_dir": "backups",
            },
        )
        _ran(resp, "POST /system/backup-settings")

    async def test_save_camera_settings(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        resp = await auth_client.post(
            f"/cameras/{CAMERA_ID}/settings",
            data={
                "capture_method": "auto",
                "rtsp_quality": "high",
                "enabled_intervals": [60],
                "is_active": "on",
            },
        )
        _ran(resp, "POST /cameras/{camera_id}/settings")


class TestProtectBackedPosts:
    """Routes that reach out to UniFi Protect.

    No NVR is reachable from the suite, so these exercise the failure branch — which is the
    branch that renders an error partial, and therefore exactly the kind of rarely-run
    template code a dependency bump breaks silently.
    """

    async def test_test_protect_connection(self, auth_client: AsyncClient) -> None:
        resp = await auth_client.post(
            "/cameras/fetch-settings/test-protect-connection",
            data={"base_url": "https://192.0.2.1", "api_key": "not-a-real-key"},
        )
        _ran(resp, "POST /cameras/fetch-settings/test-protect-connection")

    async def test_detect_camera_capabilities(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        resp = await auth_client.post(f"/cameras/{CAMERA_ID}/detect")
        _ran(resp, "POST /cameras/{camera_id}/detect")


class TestJobCreationPosts:
    """Timelapse creation forms — rejected for want of captures, having run the validation."""

    async def test_create_timelapse(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        resp = await auth_client.post(
            "/timelapses/create",
            data={
                "camera_id": CAMERA_ID,
                "date": business_day().isoformat(),
                "interval": "60",
            },
        )
        _ran(resp, "POST /timelapses/create")

    async def test_create_historical_timelapse(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        resp = await auth_client.post(
            "/timelapses/historical",
            data={
                "camera_id": CAMERA_ID,
                "start_date": "2026-01-01",
                "end_date": "2026-01-01",
                "start_time": "00:00",
                "end_time": "23:59",
                "interval": "60",
                "output_mode": "per_day",
            },
        )
        _ran(resp, "POST /timelapses/historical")

    async def test_cleanup_stale_jobs(self, auth_client: AsyncClient) -> None:
        _ran(
            await auth_client.post("/timelapses/jobs/cleanup-stale"),
            "POST /timelapses/jobs/cleanup-stale",
        )


class TestUserPosts:
    """User management, including the first-run setup route."""

    async def test_create_and_update_user(self, auth_client: AsyncClient) -> None:
        created = await auth_client.post(
            "/system/users",
            data={
                "username": "smoke-created-user",
                "password": "smoke-password-123",
                "confirm_password": "smoke-password-123",
                "is_admin": "false",
            },
        )
        _ran(created, "POST /system/users")

        async with async_session() as db:
            row = (
                await db.execute(
                    select(User).where(User.username == "smoke-created-user")
                )
            ).scalar_one_or_none()

        if row is not None:
            _ran(
                await auth_client.post(
                    f"/system/users/{row.id}",
                    data={"username": "smoke-created-user", "is_admin": "false"},
                ),
                "POST /system/users/{user_id}",
            )
            _ran(
                await auth_client.delete(f"/system/users/{row.id}"),
                "DELETE /system/users/{user_id}",
            )
        else:
            # Creation was rejected (validation, or a user already exists) — the route still
            # ran, which is what this file asserts. Exercise the other two against an id
            # that does not exist so they are requested too.
            _ran(
                await auth_client.post(
                    "/system/users/999999",
                    data={"username": "nobody", "is_admin": "false"},
                ),
                "POST /system/users/{user_id}",
            )
            _ran(
                await auth_client.delete("/system/users/999999"),
                "DELETE /system/users/{user_id}",
            )

    async def test_setup_post(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/setup",
            data={
                "username": "setup-smoke-user",
                "password": "setup-password-123",
                "confirm_password": "setup-password-123",
            },
        )
        _ran(resp, "POST /setup")


class TestDeletes:
    """Deletions, last: they remove the rows the other tests read."""

    async def test_delete_job(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        _ran(
            await auth_client.delete(f"/timelapses/job/{JOB_ID}"),
            "DELETE /timelapses/job/{job_id}",
        )

    async def test_delete_timelapse(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        _ran(
            await auth_client.delete(f"/timelapses/{seeded['timelapse_id']}"),
            "DELETE /timelapses/{timelapse_id}",
        )

    async def test_delete_camera(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        _ran(
            await auth_client.delete(f"/cameras/{CAMERA_ID}"),
            "DELETE /cameras/{camera_id}",
        )

    async def test_delete_images(
        self, auth_client: AsyncClient, seeded: dict[str, Any]
    ) -> None:
        # Scoped to the seeded camera so it cannot touch another test's rows.
        _ran(
            await auth_client.delete(
                "/images/delete",
                params={"camera": CAMERA_ID, "interval": "60"},
            ),
            "DELETE /images/delete",
        )
