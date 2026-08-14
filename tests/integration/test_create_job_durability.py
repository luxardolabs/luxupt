"""Durability lock — TimelapsesViewService.create_and_start_job (db-mutations §5e/§7).

The method must NOT commit the handed session (fw.no_redundant_commit) — get_db owns the
commit. The JobProcessor kickoff is deferred to after_commit so the worker reads a COMMITTED
job row (§5e cross-process read). This locks both properties:
  - the job is invisible to a separate connection until the OWNER commits (bite: a
    self.db.commit() in the method makes it durable early);
  - start_job fires only after the commit (bite: an inline pre-commit kickoff).
"""

import types
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.views.timelapses_view_service as tvs_mod
from app.models.camera_model import Camera
from app.models.job_model import Job
from app.services.core.camera_core_service import CameraCoreService
from app.services.core.job_core_service import JobCoreService
from app.services.views.timelapses_view_service import TimelapsesViewService

Maker = async_sessionmaker[AsyncSession]


def _svc(session: AsyncSession) -> TimelapsesViewService:
    # Only camera_service + job_service are exercised by create_and_start_job.
    return TimelapsesViewService(
        session,
        CameraCoreService(session),
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        JobCoreService(session),
        None,  # type: ignore[arg-type]
    )


async def _seed_camera(session: AsyncSession) -> None:
    session.add(
        Camera(
            camera_id="cam-1",
            name="Front Door",
            safe_name="front_door",
            video_mode="default",
            supports_full_hd_snapshot=False,
            state="CONNECTED",
        )
    )
    await session.flush()


async def _job_count(maker: Maker) -> int:
    async with maker() as s:
        return await s.scalar(select(func.count()).select_from(Job)) or 0


async def test_job_not_committed_until_owner_and_kickoff_deferred(
    durable_db: Maker, monkeypatch
) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        tvs_mod,
        "get_job_processor",
        lambda: types.SimpleNamespace(start_job=lambda *a, **k: calls.append(a)),
    )

    async with durable_db() as owner:
        await _seed_camera(owner)
        await owner.commit()

    async with durable_db() as owner:
        svc = _svc(owner)
        result = await svc.create_and_start_job(
            camera_id="cam-1", date_str="2026-01-01", interval=60
        )
        assert result["success"] is True
        # The method did NOT commit the handed session: a separate connection sees no job,
        # and the worker has not been kicked off.
        assert await _job_count(durable_db) == 0
        assert calls == []
        await owner.commit()

    # After the owner commits: the job is durable and the kickoff fired (post-commit).
    assert await _job_count(durable_db) == 1
    assert len(calls) == 1
