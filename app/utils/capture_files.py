"""Filesystem cleanup for deleted captures (sync, best-effort).

Lives in utils (not the core service) so the irreversible unlink/rmtree run outside the
request-path service: CaptureCleanupCoreService defers this to after_commit (ADR-003 /
fw.side_effects_after_commit), so a rollback never leaves capture rows pointing at deleted
files. Runs in a worker thread — never inline on the request path.
"""

import shutil
from pathlib import Path
from typing import Any

from app import config
from app.logging_config import get_logger

logger = get_logger(__name__)


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
