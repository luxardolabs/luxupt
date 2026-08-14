"""Capture cleanup service for deleting images, thumbnails, and DB records."""

import asyncio
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import capture_crud
from app.db import maintenance as db_maintenance
from app.db.post_commit import after_commit
from app.logging_config import get_logger
from app.utils.capture_files import delete_capture_files

logger = get_logger(__name__)


class CaptureCleanupCoreService:
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
            await db_maintenance.incremental_vacuum(self.db)

        # Phase 3: file + thumbnail cleanup, deferred to AFTER get_db commits (ADR-003 /
        # fw.side_effects_after_commit) — a rollback then never leaves capture rows pointing
        # at deleted files. Runs in a worker thread so the response still returns immediately.
        if file_info:
            after_commit(
                self.db,
                lambda: asyncio.create_task(
                    asyncio.to_thread(delete_capture_files, file_info)
                ),
            )

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


async def get_capture_cleanup_service(db: AsyncSession) -> CaptureCleanupCoreService:
    """Factory function to create CaptureCleanupCoreService instance."""
    return CaptureCleanupCoreService(db)
