"""Deleting captures removes their own thumbnails only.

Thumbnails are foldered by camera safe_name, so a camera replaced under the same name (a new
camera_id) shares the folder with the old one's.
"""

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.crud.capture_crud import capture_crud
from app.models.capture_model import Capture
from app.models.enum_model import CaptureMethod, CaptureStatus
from app.utils.capture_files import delete_capture_files

DAY = date(2026, 1, 1)


async def test_deleting_one_camera_spares_a_same_named_cameras_thumbnails(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "THUMBNAIL_CACHE_PATH", tmp_path / "thumbs")
    thumbs = tmp_path / "thumbs" / "cam_a" / "60s" / "2026" / "01" / "01"
    await asyncio.to_thread(thumbs.mkdir, parents=True)
    for camera_id, ts in (("old-cam", 100), ("old-cam", 200), ("new-cam", 300)):
        db.add(
            Capture(
                camera_id=camera_id,
                camera_safe_name="cam_a",
                timestamp=ts,
                capture_datetime=datetime.fromtimestamp(ts, UTC),
                capture_date=DAY,
                interval=60,
                status=CaptureStatus.SUCCESS,
                capture_method=CaptureMethod.API,
            )
        )
        await asyncio.to_thread((thumbs / f"{ts}_200.webp").write_bytes, b"t")
    await db.flush()

    file_info = await capture_crud.get_file_paths_by_filters(
        db, camera="old-cam", capture_date=DAY, interval=60
    )
    await asyncio.to_thread(delete_capture_files, file_info)

    assert sorted(path.name for path in thumbs.iterdir()) == ["300_200.webp"]
