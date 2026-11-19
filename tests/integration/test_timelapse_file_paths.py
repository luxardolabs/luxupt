"""A timelapse's video and thumbnail are served only from inside the video directory.

A row whose stored path leaves the video directory, by `..` or a symlink, serves nothing and
logs the attempt, so a tampered or stale row can never hand out an arbitrary file.
"""

import asyncio
import logging
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import config
from app.models.enum_model import TimelapseStatus
from app.models.timelapse_model import Timelapse
from app.services.core.timelapse_browser_core_service import TimelapseBrowserCoreService

Maker = async_sessionmaker[AsyncSession]


@pytest.fixture
def videos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "videos"
    root.mkdir()
    monkeypatch.setattr(config, "VIDEO_OUTPUT_PATH", root)
    return root


async def _seed(maker: Maker, file_path: str | None, thumbnail_path: str | None) -> int:
    async with maker() as owner:
        tl = Timelapse(
            camera_id="cam-1",
            camera_safe_name="front_door",
            timelapse_date=date(2026, 1, 1),
            interval=60,
            frame_count=100,
            frame_rate=30,
            duration_seconds=3.3,
            status=TimelapseStatus.COMPLETED,
            file_path=file_path,
            thumbnail_path=thumbnail_path,
        )
        owner.add(tl)
        await owner.flush()
        pk = tl.id
        await owner.commit()
    return pk


async def _touch(path: Path) -> None:
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, b"x")


async def test_files_inside_the_video_directory_are_served(
    durable_db: Maker, videos: Path
) -> None:
    video, thumb = (
        videos / "2026" / "01" / "c.mp4",
        videos / "2026" / "01" / "c_thumb.jpg",
    )
    await _touch(video)
    await _touch(thumb)
    pk = await _seed(durable_db, str(video), str(thumb))

    async with durable_db() as session:
        svc = TimelapseBrowserCoreService(session)
        assert await svc.get_video_path(pk) == str(video)
        assert await svc.get_thumbnail_path(pk) == str(thumb)


async def test_a_thumbnail_falls_back_to_the_one_beside_the_video(
    durable_db: Maker, videos: Path
) -> None:
    video, beside = videos / "c.mp4", videos / "c_thumb.jpg"
    await _touch(video)
    await _touch(beside)
    pk = await _seed(durable_db, str(video), None)

    async with durable_db() as session:
        assert await TimelapseBrowserCoreService(session).get_thumbnail_path(pk) == str(
            beside
        )


async def test_a_path_out_of_the_video_directory_serves_nothing(
    durable_db: Maker,
    videos: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = tmp_path / "secret.txt"
    await _touch(secret)
    escape = str(videos / ".." / "secret.txt")
    pk = await _seed(durable_db, escape, escape)

    async with durable_db() as session:
        svc = TimelapseBrowserCoreService(session)
        with caplog.at_level(logging.WARNING):
            assert await svc.get_video_path(pk) is None
            assert await svc.get_thumbnail_path(pk) is None
    assert "Path traversal attempt blocked for video" in caplog.messages


async def test_a_symlink_out_of_the_video_directory_serves_nothing(
    durable_db: Maker, videos: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.mp4"
    await _touch(secret)
    link = videos / "link.mp4"
    await asyncio.to_thread(link.symlink_to, secret)
    pk = await _seed(durable_db, str(link), None)

    async with durable_db() as session:
        assert await TimelapseBrowserCoreService(session).get_video_path(pk) is None


async def test_a_row_without_a_file_serves_nothing(
    durable_db: Maker, videos: Path
) -> None:
    pk = await _seed(durable_db, None, None)

    async with durable_db() as session:
        svc = TimelapseBrowserCoreService(session)
        assert await svc.get_video_path(pk) is None
        assert await svc.get_thumbnail_path(pk) is None
