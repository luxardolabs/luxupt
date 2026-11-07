"""Filesystem cleanup for deleted captures (sync, best-effort).

Lives in utils (not the core service) so the irreversible unlink/rmtree run outside the
request-path service: CaptureCleanupCoreService defers this to after_commit (ADR-003 /
fw.side_effects_after_commit), so a rollback never leaves capture rows pointing at deleted
files. Runs in a worker thread — never inline on the request path.
"""

import logging
import os
import shutil
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from app.core import config

logger = logging.getLogger(__name__)


def delete_capture_files(file_info: list[dict[str, Any]]) -> tuple[int, int]:
    """Delete image files and their thumbnail directories for deleted captures.

    Each item carries camera (safe_name), date, interval, file_path. Returns
    (files_deleted, thumb_dirs_deleted). Best-effort: a failed delete is logged, not raised.
    """
    files_deleted = 0
    for item in file_info:
        raw = item.get("file_path")
        if raw:
            try:
                fp = Path(raw)
                if fp.exists():
                    fp.unlink()
                    files_deleted += 1
            except Exception as e:
                logger.warning(
                    "Failed to delete image file", extra={"path": raw, "error": str(e)}
                )

    return files_deleted, _delete_thumbnail_dirs(file_info)


def delete_frame_files(file_info: list[dict[str, Any]]) -> tuple[int, int]:
    """Delete these frames' image files and only their own thumbnails, never the day's others.

    Each item carries camera (safe_name), date, interval, timestamp, file_path. Returns
    (files_deleted, thumbnails_deleted). Best-effort: a failed delete is logged, not raised.
    """
    files_deleted = 0
    timestamps_by_dir: dict[Path, set[str]] = defaultdict(set)
    for item in file_info:
        if item["file_path"] and _unlink(Path(item["file_path"])):
            files_deleted += 1
        timestamps_by_dir[_thumbnail_dir(item)].add(str(item["timestamp"]))

    # One listing per folder: thumbnails are named {timestamp}_{size}.webp.
    thumbnails_deleted = 0
    for thumb_dir, timestamps in timestamps_by_dir.items():
        try:
            entries = list(os.scandir(thumb_dir))
        except FileNotFoundError:
            continue
        for entry in entries:
            timestamp, _, rest = entry.name.partition("_")
            if (
                rest.endswith(".webp")
                and timestamp in timestamps
                and _unlink(Path(entry.path))
            ):
                thumbnails_deleted += 1
    return files_deleted, thumbnails_deleted


def _unlink(path: Path) -> bool:
    """Remove one file; False when it was already gone or could not be removed (logged)."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.warning(
            "Failed to delete frame file", extra={"path": str(path), "error": str(e)}
        )
        return False
    return True


def _thumbnail_dir(item: dict[str, Any]) -> Path:
    """The thumbnail cache folder of one capture's camera, interval and day."""
    camera: str = item["camera"]
    day: date = item["date"]
    return (
        config.THUMBNAIL_CACHE_PATH
        / camera
        / f"{item['interval']}s"
        / f"{day.year:04d}"
        / f"{day.month:02d}"
        / f"{day.day:02d}"
    )


def _delete_thumbnail_dirs(file_info: list[dict[str, Any]]) -> int:
    """Delete each capture's thumbnail directory once (deduplicated)."""
    deleted: set[Path] = set()
    for item in file_info:
        thumb_dir = (
            config.THUMBNAIL_CACHE_PATH
            / item["camera"]
            / f"{item['interval']}s"
            / item["date"].strftime("%Y")
            / item["date"].strftime("%m")
            / item["date"].strftime("%d")
        )
        if thumb_dir not in deleted and thumb_dir.exists():
            try:
                shutil.rmtree(thumb_dir)
                deleted.add(thumb_dir)
            except Exception as e:
                logger.warning(
                    "Failed to delete thumbnail directory",
                    extra={"path": str(thumb_dir), "error": str(e)},
                )
    return len(deleted)
