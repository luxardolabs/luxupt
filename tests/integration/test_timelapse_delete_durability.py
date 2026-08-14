"""Durability lock — TimelapseBrowserCoreService.delete_timelapse (db-mutations §7, ADR-003).

The file removal is bound to the transaction: on COMMIT the row is gone and the files are
deleted (post-commit); on ROLLBACK both the row and its files survive — never a row pointing
at a deleted file. The bite: reverting to an inline pre-commit unlink makes the rollback case
delete the files while the row is rolled back — the orphan the rule exists to prevent.
"""

from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enum_model import TimelapseStatus
from app.models.timelapse_model import Timelapse
from app.services.core.timelapse_browser_core_service import TimelapseBrowserCoreService

Maker = async_sessionmaker[AsyncSession]


async def _seed(session: AsyncSession, video: Path, thumb: Path) -> int:
    video.write_bytes(b"v")
    thumb.write_bytes(b"t")
    tl = Timelapse(
        camera_id="cam-1",
        camera_safe_name="front_door",
        timelapse_date=date(2026, 1, 1),
        interval=60,
        frame_count=100,
        frame_rate=30,
        duration_seconds=3.3,
        status=TimelapseStatus.COMPLETED,
        file_path=str(video),
        thumbnail_path=str(thumb),
    )
    session.add(tl)
    await session.flush()
    return tl.id


async def test_commit_removes_row_and_files(
    durable_db: Maker, tmp_path: Path
) -> None:
    video, thumb = tmp_path / "c.mp4", tmp_path / "c_thumb.jpg"
    async with durable_db() as owner:
        pk = await _seed(owner, video, thumb)
        await owner.commit()

    async with durable_db() as owner:
        svc = TimelapseBrowserCoreService(owner)
        assert await svc.delete_timelapse(pk) is True
        # Files persist until the owner commits — the unlink is deferred to after_commit.
        assert video.exists() and thumb.exists()
        await owner.commit()

    # After commit: row gone (separate connection) and files removed.
    async with durable_db() as reader:
        assert await reader.get(Timelapse, pk) is None
    assert not video.exists()
    assert not thumb.exists()


async def test_rollback_keeps_row_and_files(
    durable_db: Maker, tmp_path: Path
) -> None:
    video, thumb = tmp_path / "c.mp4", tmp_path / "c_thumb.jpg"
    async with durable_db() as owner:
        pk = await _seed(owner, video, thumb)
        await owner.commit()

    async with durable_db() as owner:
        svc = TimelapseBrowserCoreService(owner)
        assert await svc.delete_timelapse(pk) is True
        await owner.rollback()  # the request fails after the delete

    # The ADR-003 guarantee: row AND files survive — no dangling reference.
    async with durable_db() as reader:
        assert await reader.get(Timelapse, pk) is not None
    assert video.exists()
    assert thumb.exists()
