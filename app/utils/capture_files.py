"""Filesystem cleanup for deleted captures (sync, best-effort).

Lives in utils (not the core service) so the irreversible unlinks run outside the
request-path service: CaptureCleanupCoreService defers this to after_commit (ADR-003 /
fw.side_effects_after_commit), so a rollback never leaves capture rows pointing at deleted
files. Runs in a worker thread — never inline on the request path.
"""

import logging
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from app.core import config

logger = logging.getLogger(__name__)


def delete_capture_files(file_info: list[dict[str, Any]]) -> tuple[int, int]:
    """Delete deleted captures' image files and only their own thumbnails.

    Thumbnail folders are named by camera safe_name, which a same-named camera shares, so
    each folder loses only the deleted captures' `{timestamp}_{size}.webp` files.

    Each item carries camera (safe_name), date, interval, timestamp, file_path. Returns
    (files_deleted, thumbnails_deleted). Best-effort: a failed delete is logged, not raised.
    """
    files_deleted = 0
    timestamps_by_dir: dict[Path, set[str]] = defaultdict(set)
    for item in file_info:
        if item["file_path"] and _unlink(Path(item["file_path"])):
            files_deleted += 1
        timestamps_by_dir[_thumbnail_dir(item)].add(str(item["timestamp"]))

    # One listing per folder.
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
            "Failed to delete capture file", extra={"path": str(path), "error": str(e)}
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
