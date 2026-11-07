"""Capture cleanup service for deleting images, thumbnails, and DB records."""

import asyncio
import logging
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import capture_crud
from app.db import maintenance as db_maintenance
from app.db.post_commit import after_commit
from app.utils.capture_files import delete_capture_files, delete_frame_files

logger = logging.getLogger(__name__)


class UnfilteredDeleteError(ValueError):
    """A capture delete named no camera, date or interval, so it would remove every capture."""


class CapturesChangedError(ValueError):
    """The captures matching a delete are no longer the number its preview showed."""


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
        expected_count: int | None = None,
        expected_timestamp_sum: int | None = None,
    ) -> dict[str, Any]:
        """Delete captures matching filters including files, thumbnails, and DB records.

        DB deletion is synchronous (fast bulk SQL DELETE). File and thumbnail cleanup
        runs in the background so the HTMX response returns immediately.

        Args:
            camera: Camera ID (None = all cameras)
            capture_date: Date (None = all dates)
            interval: Interval in seconds (None = all intervals)
            expected_count: How many captures the preview showed. Any other number now means the
                set changed, so nothing is deleted: the user never removes frames they did not see.
            expected_timestamp_sum: The sum of those captures' timestamps, which tells the same
                number of different frames apart: a steady camera returns to its count every day.

        Returns:
            Dict with db_records_deleted, files_to_clean, background_cleanup

        Raises:
            UnfilteredDeleteError: nothing narrows the delete. The test is truthiness because that
                is what the crud filters on: an interval of 0 narrows nothing there.
            CapturesChangedError: the count or the timestamp sum no longer matches.
        """
        if not (camera or capture_date or interval):
            raise UnfilteredDeleteError

        if expected_count is not None:
            preview = await self.get_deletion_preview(
                camera=camera, capture_date=capture_date, interval=interval
            )
            if (preview["total_count"], preview["timestamp_sum"]) != (
                expected_count,
                expected_timestamp_sum,
            ):
                raise CapturesChangedError

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
                # post-db-filter: file_info is loaded to delete the files; this counts that list
                "files_queued": len(file_info),
                "camera": camera,
                "date": str(capture_date) if capture_date else None,
                "interval": interval,
            },
        )

        return {
            "db_records_deleted": count,
            # post-db-filter: counted off the file list already loaded for deletion
            "files_to_clean": len([f for f in file_info if f["file_path"]]),
            "background_cleanup": True,
        }

    async def delete_frames(
        self,
        *,
        camera_safe_name: str,
        interval: int,
        capture_date: date,
        frames: list[tuple[int, str]],
        listed_at: int,
    ) -> dict[str, Any]:
        """Delete exactly these frames of one camera: their rows, image files and thumbnails.

        Args:
            camera_safe_name: The camera folder the frames were found in.
            interval: The interval folder the frames were found in.
            capture_date: The day folder the frames were found in.
            frames: (timestamp, file_path) pairs, file_path as found on disk, all in one
                folder. A capture goes when its camera, interval, timestamp, file name and
                folder match one pair, so a frame that is not listed keeps its row, its file
                and its thumbnails.
            listed_at: When the frames were listed. The day's captures with no file (failed
                captures) up to this instant go too.

        Returns:
            Dict with db_records_deleted and files_to_clean.
        """
        if not frames:
            return {"db_records_deleted": 0, "files_to_clean": 0}
        file_info = await capture_crud.get_frames(
            self.db, camera_safe_name=camera_safe_name, interval=interval, frames=frames
        )
        # Each file goes by the path it was found at, which a stored path may spell differently.
        found_at = {Path(file_path).name: file_path for _, file_path in frames}
        for item in file_info:
            name = item["file_name"] or Path(item["file_path"]).name
            item["file_path"] = found_at.get(name, item["file_path"])
        count = await capture_crud.bulk_delete_frames(
            self.db, camera_safe_name=camera_safe_name, interval=interval, frames=frames
        )
        count += await capture_crud.bulk_delete_fileless(
            self.db,
            camera_safe_name=camera_safe_name,
            interval=interval,
            capture_date=capture_date,
            listed_at=listed_at,
        )
        # File + thumbnail cleanup after the commit, as in delete_by_filters (ADR-003).
        if file_info:
            after_commit(
                self.db,
                lambda: asyncio.create_task(
                    asyncio.to_thread(delete_frame_files, file_info)
                ),
            )
        return {
            "db_records_deleted": count,
            # post-db-filter: counted off the file list already loaded for deletion
            "files_to_clean": len(file_info),
        }

    async def get_deletion_preview(
        self,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
    ) -> dict[str, Any]:
        """Get preview of what would be deleted.

        Returns dict with preview list, total_count, and total_size.
        """
        preview = await capture_crud.get_deletion_preview(
            self.db,
            camera=camera,
            capture_date=capture_date,
            interval=interval,
        )

        # post-db-filter: each preview row is already a SQL COUNT/SUM per group; these add the
        # handful of groups the page lists anyway
        total_count = sum(item["count"] for item in preview)
        # post-db-filter: as above, the per-group SQL sums added up
        total_size = sum(item["total_size"] for item in preview)

        return {
            "preview": preview,
            "total_count": total_count,
            "total_size": total_size,
            # post-db-filter: as above, the per-group SQL sums added up
            "timestamp_sum": sum(item["timestamp_sum"] for item in preview),
        }


async def get_capture_cleanup_service(db: AsyncSession) -> CaptureCleanupCoreService:
    """Factory function to create CaptureCleanupCoreService instance."""
    return CaptureCleanupCoreService(db)
