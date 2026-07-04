"""Capture cleanup service for deleting images, thumbnails, and DB records."""

import asyncio
import shutil
from datetime import date
from pathlib import Path

import config
from crud import capture_crud
from logging_config import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class CaptureCleanupService:
    """Handles cleanup of captures including files, thumbnails, and DB records."""

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    async def delete_by_filters(
        self,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
    ) -> dict:
        """Delete captures matching filters including files, thumbnails, and DB records.

        DB deletion is synchronous (fast bulk SQL DELETE). File and thumbnail cleanup
        runs in the background so the HTMX response returns immediately.

        Args:
            camera: Camera ID (None = all cameras)
            capture_date: Date (None = all dates)
            interval: Interval in seconds (None = all intervals)

        Returns:
            Dict with db_records_deleted, files_to_clean, background_cleanup
        """
        # Phase 1: Get file paths BEFORE deleting from DB (lightweight SELECT)
        file_info = await capture_crud.get_file_paths_by_filters(
            self.db,
            camera=camera,
            capture_date=capture_date,
            interval=interval,
        )

        # Phase 2: Bulk delete DB records (single SQL DELETE)
        count = await capture_crud.bulk_delete_by_filters(
            self.db,
            camera=camera,
            capture_date=capture_date,
            interval=interval,
        )

        # Phase 2.5: Reclaim freed pages immediately after bulk delete
        if count > 0:
            await self.db.execute(text("PRAGMA incremental_vacuum(1000)"))

        # Phase 3: Background file + thumbnail cleanup
        if file_info:
            asyncio.create_task(self._cleanup_files_background(file_info))

        logger.info(
            "Capture DB cleanup completed, file cleanup running in background",
            extra={
                "db_records": count,
                "files_queued": len(file_info),
                "camera": camera,
                "date": str(capture_date) if capture_date else None,
                "interval": interval,
            },
        )

        return {
            "db_records_deleted": count,
            "files_to_clean": len([f for f in file_info if f["file_path"]]),
            "background_cleanup": True,
        }

    async def _cleanup_files_background(self, file_info: list[dict]) -> None:
        """Delete image files and thumbnail dirs in a thread pool (non-blocking)."""
        files_deleted, thumb_dirs_deleted = await asyncio.to_thread(self._cleanup_files_sync, file_info)

        logger.info(
            "Background file cleanup completed",
            extra={
                "files_deleted": files_deleted,
                "thumb_dirs": thumb_dirs_deleted,
            },
        )

    def _cleanup_files_sync(self, file_info: list[dict]) -> tuple[int, int]:
        """Delete image files and thumbnail dirs. Runs in thread pool."""
        files_deleted = 0
        for item in file_info:
            if item["file_path"]:
                try:
                    file_path = Path(item["file_path"])
                    if file_path.exists():
                        file_path.unlink()
                        files_deleted += 1
                except Exception as e:
                    logger.warning(
                        "Failed to delete image file",
                        extra={"path": item["file_path"], "error": str(e)},
                    )

        thumb_dirs_deleted = self._delete_thumbnail_dirs(file_info)
        return files_deleted, thumb_dirs_deleted

    def delete_thumbnail_dir(
        self,
        camera: str,
        capture_date: date,
        interval: int,
    ) -> bool:
        """Delete thumbnail directory for a specific camera/date/interval.

        Returns True if directory was deleted, False otherwise.
        """
        thumb_dir = (
            config.THUMBNAIL_CACHE_PATH
            / camera
            / f"{interval}s"
            / capture_date.strftime("%Y")
            / capture_date.strftime("%m")
            / capture_date.strftime("%d")
        )

        if thumb_dir.exists():
            try:
                shutil.rmtree(thumb_dir)
                logger.debug("Deleted thumbnail directory", extra={"path": str(thumb_dir)})
                return True
            except Exception as e:
                logger.warning(
                    "Failed to delete thumbnail directory",
                    extra={"path": str(thumb_dir), "error": str(e)},
                )
        return False

    def _delete_thumbnail_dirs(self, deleted_info: list[dict]) -> int:
        """Delete thumbnail directories for deleted captures.

        Returns count of directories deleted.
        """
        deleted_dirs: set[Path] = set()

        for item in deleted_info:
            thumb_dir = (
                config.THUMBNAIL_CACHE_PATH
                / item["camera"]
                / f"{item['interval']}s"
                / item["date"].strftime("%Y")
                / item["date"].strftime("%m")
                / item["date"].strftime("%d")
            )

            if thumb_dir not in deleted_dirs and thumb_dir.exists():
                try:
                    shutil.rmtree(thumb_dir)
                    deleted_dirs.add(thumb_dir)
                except Exception as e:
                    logger.warning(
                        "Failed to delete thumbnail directory",
                        extra={"path": str(thumb_dir), "error": str(e)},
                    )

        return len(deleted_dirs)

    async def get_deletion_preview(
        self,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
    ) -> dict:
        """Get preview of what would be deleted.

        Returns dict with preview list, total_count, and total_size.
        """
        preview = await capture_crud.get_deletion_preview(
            self.db,
            camera=camera,
            capture_date=capture_date,
            interval=interval,
        )

        total_count = sum(item["count"] for item in preview)
        total_size = sum(item["total_size"] for item in preview)

        return {
            "preview": preview,
            "total_count": total_count,
            "total_size": total_size,
        }


async def get_capture_cleanup_service(db: AsyncSession) -> CaptureCleanupService:
    """Factory function to create CaptureCleanupService instance."""
    return CaptureCleanupService(db)
