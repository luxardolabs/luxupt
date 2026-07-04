"""Activity service for activity logging and retrieval."""

import config
from crud import activity_crud
from models.activity import Activity
from schemas.activity import ActivitySummary
from sqlalchemy.ext.asyncio import AsyncSession


class ActivityService:
    """Service for activity log operations."""

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    async def get_recent(
        self,
        *,
        limit: int = config.DEFAULT_PAGE_SIZE,
        activity_type: str | None = None,
        camera_id: str | None = None,
    ) -> list[Activity]:
        """Get recent activities with optional filtering."""
        return await activity_crud.get_recent(
            self.db,
            limit=limit,
            activity_type=activity_type,
            camera_id=camera_id,
        )

    async def get_summary(self, *, hours: int = 24) -> ActivitySummary:
        """Get activity summary for the specified time period."""
        return await activity_crud.get_summary(self.db, hours=hours)

    async def log_capture_success(
        self,
        *,
        camera_id: str,
        camera_safe_name: str,
        interval: int,
        file_path: str,
    ) -> Activity:
        """Log a successful capture."""
        return await activity_crud.log_capture_success(
            self.db,
            camera_id=camera_id,
            camera_safe_name=camera_safe_name,
            interval=interval,
            file_path=file_path,
        )

    async def log_capture_failed(
        self,
        *,
        camera_id: str,
        camera_safe_name: str,
        interval: int,
        error: str,
    ) -> Activity:
        """Log a failed capture."""
        return await activity_crud.log_capture_failed(
            self.db,
            camera_id=camera_id,
            camera_safe_name=camera_safe_name,
            interval=interval,
            error=error,
        )

    async def log_timelapse_completed(
        self,
        *,
        camera_safe_name: str,
        target_date: str,
        interval: int,
        output_file: str | None = None,
    ) -> Activity:
        """Log a timelapse creation completed."""
        return await activity_crud.log_timelapse_completed(
            self.db,
            camera_safe_name=camera_safe_name,
            target_date=target_date,
            interval=interval,
            output_file=output_file,
        )

    async def log_timelapse_failed(
        self,
        *,
        camera_safe_name: str,
        target_date: str,
        interval: int,
        error: str,
    ) -> Activity:
        """Log a failed timelapse creation."""
        return await activity_crud.log_timelapse_failed(
            self.db,
            camera_safe_name=camera_safe_name,
            target_date=target_date,
            interval=interval,
            error=error,
        )


async def get_activity_service(db: AsyncSession) -> ActivityService:
    """Factory function to create ActivityService instance."""
    return ActivityService(db)
