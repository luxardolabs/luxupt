"""The job lists render with live jobs in them, not only empty.

The route smoke requests every page, but on an empty database the running/pending/completed
loops never execute, so a card that reads a context key only some routes pass (StrictUndefined
raises) is invisible to it. Seed one job in each state and render every surface that lists them.
"""

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import job_crud


@pytest.fixture
async def jobs_in_every_state(db: AsyncSession) -> dict[str, str]:
    async def _job(title: str) -> str:
        job = await job_crud.create_job(
            db,
            title=title,
            camera_safe_name="front_door",
            target_date=date(2026, 10, 1),
            interval=60,
        )
        return job.job_id

    running = await _job("running")
    await job_crud.start_job(db, running)
    pending = await _job("pending")
    done = await _job("done")
    await job_crud.start_job(db, done)
    await job_crud.complete_job(db, done, output_file="x.mp4")
    return {"running": running, "pending": pending, "completed": done}


@pytest.mark.parametrize(
    "path",
    [
        "/timelapses",
        "/timelapses/jobs",
        "/timelapses/partials/jobs",
        "/timelapses/partials/completed",
    ],
)
async def test_job_lists_render_with_jobs(
    auth_client: AsyncClient, jobs_in_every_state: dict[str, str], path: str
) -> None:
    resp = await auth_client.get(path)
    assert resp.status_code == 200, resp.text[:500]


async def test_polled_job_card_renders(
    auth_client: AsyncClient, jobs_in_every_state: dict[str, str]
) -> None:
    for job_id in jobs_in_every_state.values():
        resp = await auth_client.get(f"/timelapses/partials/job/{job_id}")
        assert resp.status_code == 200, resp.text[:500]
        assert f"job-{job_id}" in resp.text
