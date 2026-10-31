"""Capture service for managing snapshot captures with database integration."""

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.crud import capture_crud
from app.schemas.capture_schema import CaptureRead, CaptureStats
from app.services.core._path_security import validate_image_path
from app.services.core._protect_calls_durable import read_capture
from app.utils import async_fs

if TYPE_CHECKING:
    pass


class CaptureCoreService:
    """Service for managing captures with database integration."""

    def __init__(self, db: AsyncSession):
        """Initialize capture service with database session."""
        self.db = db

    async def get_latest_captures_all(self) -> dict[str, CaptureRead]:
        """Get the latest capture for each camera."""
        latest = await capture_crud.get_latest_per_camera(self.db)
        return {camera: CaptureRead.model_validate(c) for camera, c in latest.items()}

    async def get_capture_stats(self) -> CaptureStats:
        """Get overall capture statistics."""
        return await capture_crud.get_stats(self.db)

    async def get_latest_by_camera(self, camera_id: str) -> CaptureRead | None:
        """Get the latest capture for a camera."""
        capture = await capture_crud.get_latest_by_camera(self.db, camera_id)
        return CaptureRead.model_validate(capture) if capture else None

    async def get_by_camera_and_timestamp(
        self,
        camera_id: str,
        timestamp: int,
        interval: int | None = None,
    ) -> CaptureRead | None:
        """Get a specific capture by camera and timestamp."""
        capture = await capture_crud.get_by_camera_and_timestamp(
            self.db,
            camera_id,
            timestamp,
            interval,
        )
        return CaptureRead.model_validate(capture) if capture else None

    async def get_validated_file_path(
        self,
        camera_id: str,
        timestamp: int,
        interval: int | None = None,
    ) -> tuple[str | None, bool]:
        """Get a capture's file path with path traversal protection.

        The row is read in its own committed transaction, so the disk check after it runs with
        no transaction open.

        Returns:
            Tuple of (validated_file_path, exists_on_disk)
            Returns (None, False) if capture not found or path validation fails.
        """
        capture = await read_capture(camera_id, timestamp, interval)

        if not capture or not capture.file_path:
            return None, False

        # Validate path is within allowed directory
        validated_path = validate_image_path(
            capture.file_path,
            camera_id=camera_id,
            timestamp=timestamp,
        )

        if not validated_path:
            return None, False

        exists = await async_fs.path_exists(Path(validated_path))
        return validated_path, exists

    async def get_available_intervals(
        self,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
    ) -> list[int]:
        """Get list of intervals used."""
        return await capture_crud.get_available_intervals(
            self.db,
            camera=camera,
            capture_date=capture_date,
        )

    async def get_available_dates(
        self,
        *,
        camera: str | None = None,
    ) -> list[date]:
        """Get list of dates with captures."""
        return await capture_crud.get_available_dates(self.db, camera=camera)

    async def count_by_filters(
        self,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
        status: str | None = None,
    ) -> int:
        """Count captures matching filters."""
        return await capture_crud.count_by_filters(
            self.db,
            camera=camera,
            capture_date=capture_date,
            interval=interval,
            status=status,
        )

    async def get_by_filters(
        self,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = config.DEFAULT_PAGE_SIZE,
    ) -> list[CaptureRead]:
        """Get captures matching filters with pagination."""
        rows = await capture_crud.get_by_filters(
            self.db,
            camera=camera,
            capture_date=capture_date,
            interval=interval,
            status=status,
            skip=skip,
            limit=limit,
        )
        return [CaptureRead.model_validate(c) for c in rows]

    async def get_recent_failures(
        self, *, limit: int = config.RECENT_ITEMS_LIMIT
    ) -> list[CaptureRead]:
        """Get recent failed captures."""
        rows = await capture_crud.get_recent_failures(self.db, limit=limit)
        return [CaptureRead.model_validate(c) for c in rows]

    async def get_adjacent_image(
        self,
        *,
        camera_id: str,
        timestamp: int,
        current_id: int,
        direction: str,
        filter_camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
    ) -> CaptureRead | None:
        """Get adjacent image for navigation (prev/next)."""
        capture = await capture_crud.get_adjacent_image(
            self.db,
            camera_id=camera_id,
            timestamp=timestamp,
            current_id=current_id,
            direction=direction,
            filter_camera=filter_camera,
            capture_date=capture_date,
            interval=interval,
        )
        return CaptureRead.model_validate(capture) if capture else None

    async def get_deletion_preview(
        self,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get summary of captures that would be deleted."""
        return await capture_crud.get_deletion_preview(
            self.db,
            camera=camera,
            capture_date=capture_date,
            interval=interval,
        )

    async def get_available_cameras(self) -> list[str]:
        """Get list of cameras with captures."""
        return await capture_crud.get_available_cameras(self.db)


async def get_capture_service(db: AsyncSession) -> CaptureCoreService:
    """Factory function to create CaptureCoreService instance."""
    return CaptureCoreService(db)
