"""Durability lock — CaptureCleanupCoreService.delete_by_filters (db-mutations §7, ADR-003).

File + thumbnail cleanup is deferred to after_commit (run in a worker thread), so it fires
only after get_db commits: on COMMIT the rows are gone and the files are removed; on ROLLBACK
both the rows and files survive — never a row pointing at a deleted file. The bite: reverting
Phase 3 to the inline pre-commit asyncio.create_task deletes the files even on rollback.
"""

import asyncio
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.capture_model import Capture
from app.models.enum_model import CaptureStatus
from app.services.core.capture_cleanup_core_service import CaptureCleanupCoreService

Maker = async_sessionmaker[AsyncSession]


async def _seed(session: AsyncSession, img: Path) -> None:
    img.write_bytes(b"img")
    session.add(
        Capture(
            camera_id="cam-uuid-1",
            camera_safe_name="front_door",
            timestamp=1735732800,
            capture_datetime=datetime(2026, 1, 1, 12, 0, 0),
            capture_date=date(2026, 1, 1),
            interval=60,
            status=CaptureStatus.SUCCESS,
            file_path=str(img),
        )
    )
    await session.flush()


async def _count(maker: Maker) -> int:
    async with maker() as s:
        return await s.scalar(select(func.count()).select_from(Capture)) or 0


async def _wait_gone(path: Path, tries: int = 100) -> None:
    for _ in range(tries):
        if not path.exists():
            return
        await asyncio.sleep(0.02)


async def test_commit_deletes_rows_and_files(
    durable_db: Maker, tmp_path: Path
) -> None:
    img = tmp_path / "shot.jpg"
    async with durable_db() as owner:
        await _seed(owner, img)
        await owner.commit()

    async with durable_db() as owner:
        svc = CaptureCleanupCoreService(owner)
        await svc.delete_by_filters(
            camera="cam-uuid-1", capture_date=date(2026, 1, 1), interval=60
        )
        # Deferred: even after a scheduler tick the file is untouched pre-commit.
        await asyncio.sleep(0.05)
        assert img.exists()
        await owner.commit()

    await _wait_gone(img)  # after_commit -> worker thread removes it
    assert not img.exists()
    assert await _count(durable_db) == 0


async def test_rollback_keeps_rows_and_files(
    durable_db: Maker, tmp_path: Path
) -> None:
    img = tmp_path / "shot.jpg"
    async with durable_db() as owner:
        await _seed(owner, img)
        await owner.commit()

    async with durable_db() as owner:
        svc = CaptureCleanupCoreService(owner)
        await svc.delete_by_filters(
            camera="cam-uuid-1", capture_date=date(2026, 1, 1), interval=60
        )
        await owner.rollback()

    # after_commit never fired; give any errant task a chance, then assert survival.
    await asyncio.sleep(0.1)
    assert img.exists()
    assert await _count(durable_db) == 1
