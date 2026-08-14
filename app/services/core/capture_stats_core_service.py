"""Capture statistics service for analytics and statistics."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import capture_crud
from app.schemas.capture_schema import CaptureStats


class CaptureStatsCoreService:
    """Service for capture statistics and analytics."""

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    async def get_time_series(
        self,
        *,
        camera: str | None = None,
        interval: int | None = None,
        since_timestamp: int | None = None,
        until_timestamp: int | None = None,
        bucket_seconds: int = 3600,
    ) -> list[dict[str, Any]]:
        """Get time series data for capture duration and file size."""
        return await capture_crud.get_time_series_stats(
            self.db,
            camera=camera,
            interval=interval,
            since_timestamp=since_timestamp,
            until_timestamp=until_timestamp,
            bucket_seconds=bucket_seconds,
        )

    async def get_success_failure(
        self,
        *,
        camera: str | None = None,
        interval: int | None = None,
        since_timestamp: int | None = None,
        until_timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Get success/failure counts and percentages."""
        return await capture_crud.get_success_failure_stats(
            self.db,
            camera=camera,
            interval=interval,
            since_timestamp=since_timestamp,
            until_timestamp=until_timestamp,
        )

    async def get_success_failure_timeseries(
        self,
        *,
        camera: str | None = None,
        interval: int | None = None,
        since_timestamp: int | None = None,
        until_timestamp: int | None = None,
        bucket_seconds: int = 3600,
    ) -> list[dict[str, Any]]:
        """Get success/failure counts over time in buckets."""
        return await capture_crud.get_success_failure_timeseries(
            self.db,
            camera=camera,
            interval=interval,
            since_timestamp=since_timestamp,
            until_timestamp=until_timestamp,
            bucket_seconds=bucket_seconds,
        )

    async def get_camera_breakdown(
        self,
        *,
        interval: int | None = None,
        since_timestamp: int | None = None,
        until_timestamp: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get capture count breakdown by camera."""
        return await capture_crud.get_camera_breakdown(
            self.db,
            interval=interval,
            since_timestamp=since_timestamp,
            until_timestamp=until_timestamp,
            limit=limit,
        )

    async def get_averages(
        self,
        *,
        camera: str | None = None,
        interval: int | None = None,
        since_timestamp: int | None = None,
        until_timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Get average duration and file size for captures."""
        return await capture_crud.get_avg_stats(
            self.db,
            camera=camera,
            interval=interval,
            since_timestamp=since_timestamp,
            until_timestamp=until_timestamp,
        )

    async def get_stats(self) -> CaptureStats:
        """Get overall capture statistics."""
        return await capture_crud.get_stats(self.db)


async def get_capture_stats_service(db: AsyncSession) -> CaptureStatsCoreService:
    """Factory function to create CaptureStatsCoreService instance."""
    return CaptureStatsCoreService(db)
