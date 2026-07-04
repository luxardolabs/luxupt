"""Camera service for camera management business logic."""

from typing import Any

import config
from crud import camera_crud
from models.camera import Camera
from sqlalchemy.ext.asyncio import AsyncSession


class CameraService:
    """Service for camera management operations."""

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    async def get_by_safe_name(self, safe_name: str) -> Camera | None:
        """Get a camera by its safe name."""
        return await camera_crud.get_by_safe_name(self.db, safe_name)

    async def get_by_id(self, camera_id: str) -> Camera | None:
        """Get a camera by its UniFi camera ID."""
        return await camera_crud.get_by_camera_id(self.db, camera_id)

    async def get_active(self) -> list[Camera]:
        """Get all active cameras."""
        return await camera_crud.get_active(self.db)

    async def get_inactive(self) -> list[Camera]:
        """Get all inactive (disabled) cameras."""
        return await camera_crud.get_inactive(self.db)

    async def get_all(self, limit: int = config.MAX_PAGE_SIZE) -> list[Camera]:
        """Get all cameras."""
        return await camera_crud.get_multi(self.db, limit=limit)

    async def get_connected(self) -> list[Camera]:
        """Get all connected cameras."""
        return await camera_crud.get_connected(self.db)

    async def get_stats(self, camera_id: str, *, global_intervals: list[int] | None = None) -> dict:
        """Get statistics for a camera."""
        return await camera_crud.get_camera_stats(self.db, camera_id, global_intervals=global_intervals)

    async def update_settings(self, camera_id: str, settings: dict[str, Any]) -> Camera | None:
        """Update camera settings."""
        camera = await camera_crud.get_by_camera_id(self.db, camera_id)
        if not camera:
            return None
        return await camera_crud.update(self.db, db_obj=camera, obj_in=settings)

    async def update_capability_detection(
        self,
        camera_id: str,
        *,
        api_max_resolution: str | None = None,
        rtsp_max_resolution: str | None = None,
        recommended_method: str | None = None,
    ) -> Camera | None:
        """Update capability detection results for a camera."""
        return await camera_crud.update_capability_detection(
            self.db,
            camera_id,
            api_max_resolution=api_max_resolution,
            rtsp_max_resolution=rtsp_max_resolution,
            recommended_method=recommended_method,
        )

    async def get_cameras_for_interval(self, interval: int) -> list[Camera]:
        """Get cameras enabled for a specific interval."""
        return await camera_crud.get_cameras_for_interval(self.db, interval)

    async def upsert(self, data: dict[str, Any]) -> Camera:
        """Create or update a camera from a dictionary."""
        return await camera_crud.upsert_from_dict(self.db, data=data)

    async def update_status(
        self,
        camera_id: str,
        *,
        is_connected: bool | None = None,
        is_recording: bool | None = None,
        state: str | None = None,
    ) -> Camera | None:
        """Update camera status fields."""
        return await camera_crud.update_status(
            self.db,
            camera_id,
            is_connected=is_connected,
            is_recording=is_recording,
            state=state,
        )

    async def increment_captures(self, camera_id: str, *, success: bool = True) -> Camera | None:
        """Increment capture counts for a camera."""
        return await camera_crud.increment_captures(self.db, camera_id, success=success)

    async def set_first_discovered(self, camera_id: str) -> Camera | None:
        """Set the first_discovered_at timestamp if not already set."""
        return await camera_crud.set_first_discovered(self.db, camera_id)

    async def count(self) -> int:
        """Count total cameras."""
        return await camera_crud.count(self.db)

    async def delete(self, camera_id: str) -> bool:
        """Delete a camera by its UniFi camera ID.

        Related captures and timelapses will have their camera_db_id set to NULL.
        Files on disk are NOT deleted.

        Returns True if camera was deleted, False if not found.
        """
        return await camera_crud.delete_by_camera_id(self.db, camera_id)


async def get_camera_service(db: AsyncSession) -> CameraService:
    """Factory function to create CameraService instance."""
    return CameraService(db)
