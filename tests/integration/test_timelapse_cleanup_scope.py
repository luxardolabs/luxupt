"""After a video is built with keep_images off, the cleanup deletes exactly the frames it encoded.

Never another camera's frames, a frame of the other format or folder, one that landed during the
encode, or the thumbnail of a frame it keeps.
"""

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.crud.capture_crud import FRAME_CHUNK, capture_crud
from app.models.capture_model import Capture
from app.models.enum_model import CaptureMethod, CaptureStatus
from app.utils import capture_files
from app.utils.capture_files import delete_frame_files
from app.workers.timelapse_service import EncodingSettings, TimelapseService

DAY = date(2026, 1, 1)
INTERVAL = 60
ENCODING = EncodingSettings(
    frame_rate=30, crf=23, preset="medium", pixel_format="yuv420p", ffmpeg_timeout=60
)


def _day_dir(safe_name: str) -> Path:
    return config.IMAGE_OUTPUT_PATH / safe_name / f"{INTERVAL}s" / "2026" / "01" / "01"


def _thumb_dir(safe_name: str) -> Path:
    return (
        config.THUMBNAIL_CACHE_PATH / safe_name / f"{INTERVAL}s" / "2026" / "01" / "01"
    )


def _add_capture(
    db: AsyncSession,
    camera_id: str,
    safe_name: str,
    ts: int,
    ext: str = "jpg",
    stored_root: Path | None = None,
) -> None:
    name = f"{safe_name}_{ts}.{ext}"
    folder = _day_dir(safe_name)
    if stored_root is not None:
        folder = stored_root / folder.relative_to(config.IMAGE_OUTPUT_PATH)
    db.add(
        Capture(
            camera_id=camera_id,
            camera_safe_name=safe_name,
            file_name=name,
            file_path=str(folder / name),
            timestamp=ts,
            capture_datetime=datetime.fromtimestamp(ts, UTC),
            capture_date=DAY,
            interval=INTERVAL,
            status=CaptureStatus.SUCCESS,
            capture_method=CaptureMethod.API if ext == "jpg" else CaptureMethod.RTSP,
        )
    )


async def _frame(
    db: AsyncSession,
    camera_id: str,
    safe_name: str,
    ts: int,
    ext: str = "jpg",
    stored_root: Path | None = None,
) -> None:
    _add_capture(db, camera_id, safe_name, ts, ext, stored_root)
    folder = _day_dir(safe_name)
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread((folder / f"{safe_name}_{ts}.{ext}").write_bytes, b"img")


async def _left(db: AsyncSession, camera_id: str) -> list[tuple[str, int]]:
    rows = await db.execute(
        select(Capture.camera_safe_name, Capture.timestamp)
        .where(Capture.camera_id == camera_id)
        .order_by(Capture.timestamp)
    )
    return [(name, ts) for name, ts in rows]


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TimelapseService:
    monkeypatch.setattr(config, "IMAGE_OUTPUT_PATH", tmp_path / "images")
    monkeypatch.setattr(config, "VIDEO_OUTPUT_PATH", tmp_path / "videos")
    monkeypatch.setattr(config, "THUMBNAIL_CACHE_PATH", tmp_path / "thumbs")
    service = TimelapseService()

    async def encoded(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(service, "_create_video", encoded)
    return service


async def _build(worker: TimelapseService, safe_name: str) -> bool | str | None:
    return await worker._create_timelapse_for_camera_interval(
        safe_name,
        INTERVAL,
        datetime(2026, 1, 1, tzinfo=UTC),
        keep_images=False,
        encoding_settings=ENCODING,
    )


async def test_only_the_built_cameras_frames_go(
    db: AsyncSession, worker: TimelapseService
) -> None:
    # No camera row for either: a camera renamed in Protect since the last sync.
    for camera_id, safe_name in (("cam-a", "cam_a"), ("cam-b", "cam_b")):
        for ts in (100, 200):
            await _frame(db, camera_id, safe_name, ts)
    await db.flush()

    assert await _build(worker, "cam_a") is True

    assert await _left(db, "cam-a") == []
    assert await _left(db, "cam-b") == [("cam_b", 100), ("cam_b", 200)]


async def test_a_frame_that_arrives_during_the_encode_is_kept(
    db: AsyncSession, worker: TimelapseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    for ts in (100, 300):
        await _frame(db, "cam-a", "cam_a", ts)
    await db.flush()

    async def encode_while_frames_land(*_args: object, **_kwargs: object) -> bool:
        # A live frame after the newest one, and a backfilled one between them.
        for ts in (400, 200):
            await _frame(db, "cam-a", "cam_a", ts)
        await db.flush()
        return True

    monkeypatch.setattr(worker, "_create_video", encode_while_frames_land)

    assert await _build(worker, "cam_a") is True

    assert await _left(db, "cam-a") == [("cam_a", 200), ("cam_a", 400)]


async def test_frames_of_the_format_not_encoded_are_kept(
    db: AsyncSession, worker: TimelapseService
) -> None:
    for ts in (100, 200):
        await _frame(db, "cam-a", "cam_a", ts, ext="jpg")
    for ts in (300, 400):
        await _frame(db, "cam-a", "cam_a", ts, ext="png")
    await db.flush()

    assert await _build(worker, "cam_a") is True

    assert await _left(db, "cam-a") == [("cam_a", 100), ("cam_a", 200)]


async def test_a_renamed_cameras_old_folder_is_kept(
    db: AsyncSession, worker: TimelapseService
) -> None:
    for ts in (100, 200):
        await _frame(db, "cam-a", "old_name", ts)
    for ts in (300, 400):
        await _frame(db, "cam-a", "new_name", ts)
    await db.flush()

    assert await _build(worker, "new_name") is True

    assert await _left(db, "cam-a") == [("old_name", 100), ("old_name", 200)]


async def test_a_frame_stored_under_another_spelling_of_the_root_goes(
    db: AsyncSession, worker: TimelapseService, tmp_path: Path
) -> None:
    for ts in (100, 200):
        await _frame(db, "cam-a", "cam_a", ts, stored_root=tmp_path / "old" / "images")
    await db.flush()

    assert await _build(worker, "cam_a") is True

    assert await _left(db, "cam-a") == []
    image = _day_dir("cam_a") / "cam_a_100.jpg"
    for _ in range(100):
        if not await asyncio.to_thread(image.exists):
            break
        await asyncio.sleep(0.02)
    assert not image.exists()


async def test_the_days_failed_captures_go_up_to_the_listing(
    db: AsyncSession, worker: TimelapseService
) -> None:
    for ts in (100, 300):
        await _frame(db, "cam-a", "cam_a", ts)
    after_the_listing = int(datetime.now(UTC).timestamp()) + 3600
    for ts, day in (
        (50, DAY),
        (200, DAY),
        (400, DAY),
        (500, date(2026, 1, 2)),
        (after_the_listing, DAY),
    ):
        db.add(
            Capture(
                camera_id="cam-a",
                camera_safe_name="cam_a",
                timestamp=ts,
                capture_datetime=datetime.fromtimestamp(ts, UTC),
                capture_date=day,
                interval=INTERVAL,
                status=CaptureStatus.FAILED,
                capture_method=CaptureMethod.API,
            )
        )
    await db.flush()

    assert await _build(worker, "cam_a") is True

    assert await _left(db, "cam-a") == [("cam_a", 500), ("cam_a", after_the_listing)]


async def test_the_same_name_in_another_day_folder_is_kept(
    db: AsyncSession, worker: TimelapseService
) -> None:
    await _frame(db, "cam-a", "cam_a", 100)
    other_day = _day_dir("cam_a").parent / "02"
    db.add(
        Capture(
            camera_id="cam-a",
            camera_safe_name="cam_a",
            file_name="cam_a_100.jpg",
            file_path=str(other_day / "cam_a_100.jpg"),
            timestamp=100,
            capture_datetime=datetime.fromtimestamp(100, UTC),
            capture_date=DAY,
            interval=INTERVAL,
            status=CaptureStatus.SUCCESS,
            capture_method=CaptureMethod.API,
        )
    )
    await db.flush()

    assert await _build(worker, "cam_a") is True

    assert await _left(db, "cam-a") == [("cam_a", 100)]


async def test_thumbnails_are_listed_once_per_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "THUMBNAIL_CACHE_PATH", tmp_path / "thumbs")
    thumbs = _thumb_dir("cam_a")
    await asyncio.to_thread(thumbs.mkdir, parents=True)
    for ts in range(1, 31):
        await asyncio.to_thread((thumbs / f"{ts}_200.webp").write_bytes, b"t")
    listings: list[str] = []
    real_scandir = capture_files.os.scandir

    def counted(path: str) -> object:
        listings.append(str(path))
        return real_scandir(path)

    monkeypatch.setattr(capture_files.os, "scandir", counted)
    file_info = [
        {
            "camera": "cam_a",
            "date": DAY,
            "interval": INTERVAL,
            "timestamp": ts,
            "file_path": None,
        }
        for ts in range(1, 21)
    ]

    assert await asyncio.to_thread(delete_frame_files, file_info) == (0, 20)

    assert listings == [str(thumbs)]
    assert len(list(thumbs.iterdir())) == 10


async def test_a_stray_file_name_does_not_fail_a_built_video(
    db: AsyncSession, worker: TimelapseService
) -> None:
    await _frame(db, "cam-a", "cam_a", 100)
    await db.flush()
    stray = _day_dir("cam_a") / "cam_a_².jpg"
    await asyncio.to_thread(stray.write_bytes, b"img")

    assert await _build(worker, "cam_a") is True

    assert await _left(db, "cam-a") == []


async def test_a_day_larger_than_one_statement_goes_whole(
    db: AsyncSession, worker: TimelapseService
) -> None:
    for ts in range(1, 2 * FRAME_CHUNK + 2):
        await _frame(db, "cam-a", "cam_a", ts)
    await db.flush()

    assert await _build(worker, "cam_a") is True

    assert await _left(db, "cam-a") == []


async def test_only_the_deleted_frames_thumbnails_go(
    db: AsyncSession, worker: TimelapseService
) -> None:
    thumbs = _thumb_dir("cam_a")
    await asyncio.to_thread(thumbs.mkdir, parents=True, exist_ok=True)
    for ts in (100, 200, 300):
        await _frame(db, "cam-a", "cam_a", ts)
        for size in (200, 400):
            await asyncio.to_thread((thumbs / f"{ts}_{size}.webp").write_bytes, b"t")
    await db.flush()
    frames = [(ts, str(_day_dir("cam_a") / f"cam_a_{ts}.jpg")) for ts in (100, 200)]

    file_info = await capture_crud.get_frames(
        db, camera_safe_name="cam_a", interval=INTERVAL, frames=frames
    )
    assert await asyncio.to_thread(delete_frame_files, file_info) == (2, 4)

    left = sorted(path.name for path in thumbs.iterdir())
    assert left == ["300_200.webp", "300_400.webp"]
    assert (_day_dir("cam_a") / "cam_a_300.jpg").exists()
    assert not (_day_dir("cam_a") / "cam_a_100.jpg").exists()
