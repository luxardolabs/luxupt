"""Capture service for managing snapshot captures with database integration."""

from datetime import date, datetime
from pathlib import Path

import config
from crud import activity_crud, camera_crud, capture_crud
from models.capture import Capture
from schemas.capture import CaptureCreate, CaptureStats
from sqlalchemy.ext.asyncio import AsyncSession
from utils import async_fs

from services.path_security import validate_image_path


class CaptureService:
    """Service for managing captures with database integration."""

    def __init__(self, db: AsyncSession):
        """Initialize capture service with database session."""
        self.db = db

    async def record_capture_success(
        self,
        *,
        camera_id: str,
        camera_safe_name: str,
        timestamp: int,
        interval: int,
        file_path: str,
        file_size: int,
        capture_method: str = "api",
        capture_duration_ms: int | None = None,
    ) -> None:
        """Record a successful capture to the database."""
        capture_datetime = datetime.fromtimestamp(timestamp)

        # Get camera DB ID if available
        camera = await camera_crud.get_by_camera_id(self.db, camera_id)
        camera_db_id = camera.id if camera else None

        # Create capture record
        capture_data = CaptureCreate(
            camera_id=camera_id,
            camera_safe_name=camera_safe_name,
            timestamp=timestamp,
            capture_datetime=capture_datetime,
            capture_date=capture_datetime.date(),
            interval=interval,
            status="success",
            capture_method=capture_method,
            camera_db_id=camera_db_id,
            file_path=file_path,
            file_name=Path(file_path).name,
            file_size=file_size,
            capture_duration_ms=capture_duration_ms,
        )

        await capture_crud.create(self.db, obj_in=capture_data)

        # Update camera stats
        if camera:
            await camera_crud.increment_captures(self.db, camera_id, success=True)

        # Log activity
        await activity_crud.log_capture_success(
            self.db,
            camera_id=camera_id,
            camera_safe_name=camera_safe_name,
            interval=interval,
            file_path=file_path,
        )

    async def record_capture_failure(
        self,
        *,
        camera_id: str,
        camera_safe_name: str,
        timestamp: int,
        interval: int,
        error_message: str,
        capture_method: str = "api",
    ) -> None:
        """Record a failed capture to the database."""
        capture_datetime = datetime.fromtimestamp(timestamp)

        # Get camera DB ID if available
        camera = await camera_crud.get_by_camera_id(self.db, camera_id)
        camera_db_id = camera.id if camera else None

        # Create capture record with error
        capture_data = CaptureCreate(
            camera_id=camera_id,
            camera_safe_name=camera_safe_name,
            timestamp=timestamp,
            capture_datetime=capture_datetime,
            capture_date=capture_datetime.date(),
            interval=interval,
            status="failed",
            capture_method=capture_method,
            camera_db_id=camera_db_id,
            error_message=error_message,
        )

        await capture_crud.create(self.db, obj_in=capture_data)

        # Update camera stats
        if camera:
            await camera_crud.increment_captures(self.db, camera_id, success=False)

        # Log activity
        await activity_crud.log_capture_failed(
            self.db,
            camera_id=camera_id,
            camera_safe_name=camera_safe_name,
            interval=interval,
            error=error_message,
        )

    async def get_captures_for_date(
        self,
        *,
        camera_id: str,
        capture_date: date,
        interval: int,
    ) -> list[Capture]:
        """Get all captures for a specific camera, date, and interval."""
        return await capture_crud.get_captures_for_timelapse(
            self.db,
            camera=camera_id,
            capture_date=capture_date,
            interval=interval,
        )

    async def get_latest_capture(self, camera_id: str) -> Capture | None:
        """Get the latest capture for a camera."""
        return await capture_crud.get_latest_by_camera(self.db, camera_id)

    async def get_latest_captures_all(self) -> dict[str, Capture]:
        """Get the latest capture for each camera."""
        return await capture_crud.get_latest_per_camera(self.db)

    async def get_capture_stats(self) -> CaptureStats:
        """Get overall capture statistics."""
        return await capture_crud.get_stats(self.db)

    async def sync_cameras_from_api(self, cameras: list) -> None:
        """Sync camera list from API to database."""
        for cam in cameras:
            # Build camera data dict
            camera_data = {
                "camera_id": cam.id,
                "name": cam.name,
                "safe_name": cam.safe_name,
                "mac": cam.mac,
                "model_key": cam.model_key,
                "video_mode": cam.video_mode,
                "hdr_type": cam.hdr_type,
                "is_connected": cam.is_connected,
                "is_recording": cam.is_recording,
                "supports_full_hd_snapshot": cam.supports_full_hd_snapshot,
                "has_hdr": cam.has_hdr,
                "has_mic": cam.has_mic,
                "has_speaker": cam.has_speaker,
                "smart_detect_types": cam.smart_detect_types,
                "state": cam.state,
            }

            await camera_crud.upsert_from_dict(self.db, data=camera_data)

    async def get_latest_by_camera(self, camera_id: str) -> Capture | None:
        """Get the latest capture for a camera."""
        return await capture_crud.get_latest_by_camera(self.db, camera_id)

    async def get_by_camera_and_timestamp(
        self,
        camera_id: str,
        timestamp: int,
        interval: int | None = None,
    ) -> Capture | None:
        """Get a specific capture by camera and timestamp."""
        return await capture_crud.get_by_camera_and_timestamp(
            self.db,
            camera_id,
            timestamp,
            interval,
        )

    async def get_validated_file_path(
        self,
        camera_id: str,
        timestamp: int,
        interval: int | None = None,
    ) -> tuple[str | None, bool]:
        """Get a capture's file path with path traversal protection.

        Returns:
            Tuple of (validated_file_path, exists_on_disk)
            Returns (None, False) if capture not found or path validation fails.
        """
        capture = await self.get_by_camera_and_timestamp(camera_id, timestamp, interval)

        if not capture or not capture.file_path:
            return None, False

        # Validate path is within allowed directory
        validated_path = validate_image_path(
            capture.file_path,
            context={"camera": camera_id, "timestamp": timestamp},
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
    ) -> list[Capture]:
        """Get captures matching filters with pagination."""
        return await capture_crud.get_by_filters(
            self.db,
            camera=camera,
            capture_date=capture_date,
            interval=interval,
            status=status,
            skip=skip,
            limit=limit,
        )

    async def get_recent_failures(self, *, limit: int = config.RECENT_ITEMS_LIMIT) -> list[Capture]:
        """Get recent failed captures."""
        return await capture_crud.get_recent_failures(self.db, limit=limit)

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
    ) -> Capture | None:
        """Get adjacent image for navigation (prev/next)."""
        return await capture_crud.get_adjacent_image(
            self.db,
            camera_id=camera_id,
            timestamp=timestamp,
            current_id=current_id,
            direction=direction,
            filter_camera=filter_camera,
            capture_date=capture_date,
            interval=interval,
        )

    async def get_deletion_preview(
        self,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
    ) -> list[dict]:
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


async def get_capture_service(db: AsyncSession) -> CaptureService:
    """Factory function to create CaptureService instance."""
    return CaptureService(db)
