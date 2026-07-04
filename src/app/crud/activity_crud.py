"""CRUD operations for Activity model."""

from datetime import datetime, timedelta

import config
from models.activity import Activity, ActivityType
from pydantic import BaseModel
from schemas.activity import ActivityCreate, ActivitySummary
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crud.base import CRUDBase


class ActivityUpdate(BaseModel):
    """Placeholder for activity updates (rarely needed)."""

    pass


class CRUDActivity(CRUDBase[Activity, ActivityCreate, ActivityUpdate]):
    """CRUD operations for Activity model."""

    async def get_recent(
        self,
        db: AsyncSession,
        *,
        limit: int = config.DEFAULT_PAGE_SIZE,
        activity_type: str | None = None,
        camera_id: str | None = None,
    ) -> list[Activity]:
        """Get recent activities with optional filters."""
        query = select(Activity)

        if activity_type:
            query = query.where(Activity.activity_type == activity_type)
        if camera_id:
            query = query.where(Activity.camera_id == camera_id)

        query = query.order_by(Activity.timestamp.desc()).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_since(
        self,
        db: AsyncSession,
        since: datetime,
        *,
        activity_type: str | None = None,
        limit: int = config.MAX_PAGE_SIZE,
    ) -> list[Activity]:
        """Get activities since a timestamp."""
        query = select(Activity).where(Activity.timestamp >= since)

        if activity_type:
            query = query.where(Activity.activity_type == activity_type)

        query = query.order_by(Activity.timestamp.desc()).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def log(
        self,
        db: AsyncSession,
        *,
        activity_type: str,
        message: str,
        camera_id: str | None = None,
        camera_safe_name: str | None = None,
        interval: int | None = None,
        details: dict | None = None,
    ) -> Activity:
        """Log a new activity event."""
        activity = Activity(
            timestamp=datetime.now(),
            activity_type=activity_type,
            message=message,
            camera_id=camera_id,
            camera_safe_name=camera_safe_name,
            interval=interval,
            details=details,
        )
        db.add(activity)
        await db.flush()
        await db.refresh(activity)
        return activity

    async def log_capture_success(
        self,
        db: AsyncSession,
        *,
        camera_id: str,
        camera_safe_name: str,
        interval: int,
        file_path: str | None = None,
    ) -> Activity:
        """Log a successful capture."""
        return await self.log(
            db,
            activity_type=ActivityType.CAPTURE_SUCCESS,
            message=f"Captured image from {camera_safe_name}",
            camera_id=camera_id,
            camera_safe_name=camera_safe_name,
            interval=interval,
            details={"file_path": file_path} if file_path else None,
        )

    async def log_capture_failed(
        self,
        db: AsyncSession,
        *,
        camera_id: str,
        camera_safe_name: str,
        interval: int,
        error: str,
    ) -> Activity:
        """Log a failed capture."""
        return await self.log(
            db,
            activity_type=ActivityType.CAPTURE_FAILED,
            message=f"Failed to capture from {camera_safe_name}: {error}",
            camera_id=camera_id,
            camera_safe_name=camera_safe_name,
            interval=interval,
            details={"error": error},
        )

    async def log_timelapse_started(
        self,
        db: AsyncSession,
        *,
        camera_safe_name: str,
        target_date: str,
        interval: int,
    ) -> Activity:
        """Log timelapse creation started."""
        return await self.log(
            db,
            activity_type=ActivityType.TIMELAPSE_STARTED,
            message=f"Started timelapse for {camera_safe_name} on {target_date}",
            camera_safe_name=camera_safe_name,
            interval=interval,
            details={"date": target_date},
        )

    async def log_timelapse_completed(
        self,
        db: AsyncSession,
        *,
        camera_safe_name: str,
        target_date: str,
        interval: int,
        output_file: str | None = None,
    ) -> Activity:
        """Log timelapse creation completed."""
        return await self.log(
            db,
            activity_type=ActivityType.TIMELAPSE_COMPLETED,
            message=f"Completed timelapse for {camera_safe_name} on {target_date}",
            camera_safe_name=camera_safe_name,
            interval=interval,
            details={"date": target_date, "output_file": output_file},
        )

    async def log_timelapse_failed(
        self,
        db: AsyncSession,
        *,
        camera_safe_name: str,
        target_date: str,
        interval: int,
        error: str,
    ) -> Activity:
        """Log timelapse creation failed."""
        return await self.log(
            db,
            activity_type=ActivityType.TIMELAPSE_FAILED,
            message=f"Failed timelapse for {camera_safe_name}: {error}",
            camera_safe_name=camera_safe_name,
            interval=interval,
            details={"date": target_date, "error": error},
        )

    async def get_summary(
        self,
        db: AsyncSession,
        *,
        hours: int = 24,
    ) -> ActivitySummary:
        """Get activity summary for the last N hours in a single query.

        Uses CASE expressions to count all activity types at once,
        avoiding N+1 query pattern.
        """
        since = datetime.now() - timedelta(hours=hours)

        # Single query with CASE expressions to count each type
        result = await db.execute(
            select(
                func.count(Activity.id).label("total"),
                func.sum(case((Activity.activity_type == ActivityType.CAPTURE_SUCCESS, 1), else_=0)).label(
                    "capture_success"
                ),
                func.sum(case((Activity.activity_type == ActivityType.CAPTURE_FAILED, 1), else_=0)).label(
                    "capture_failed"
                ),
                func.sum(case((Activity.activity_type == ActivityType.TIMELAPSE_STARTED, 1), else_=0)).label(
                    "timelapse_started"
                ),
                func.sum(case((Activity.activity_type == ActivityType.TIMELAPSE_COMPLETED, 1), else_=0)).label(
                    "timelapse_completed"
                ),
                func.sum(case((Activity.activity_type == ActivityType.TIMELAPSE_FAILED, 1), else_=0)).label(
                    "timelapse_failed"
                ),
                func.sum(case((Activity.activity_type == ActivityType.ERROR, 1), else_=0)).label("error"),
                func.sum(case((Activity.activity_type == ActivityType.CAMERA_ONLINE, 1), else_=0)).label(
                    "cameras_online"
                ),
                func.sum(case((Activity.activity_type == ActivityType.CAMERA_OFFLINE, 1), else_=0)).label(
                    "cameras_offline"
                ),
            ).where(Activity.timestamp >= since)
        )
        row = result.one()

        return ActivitySummary(
            total_events=row.total or 0,
            capture_success_count=row.capture_success or 0,
            capture_failed_count=row.capture_failed or 0,
            timelapse_started_count=row.timelapse_started or 0,
            timelapse_completed_count=row.timelapse_completed or 0,
            timelapse_failed_count=row.timelapse_failed or 0,
            error_count=row.error or 0,
            cameras_online=row.cameras_online or 0,
            cameras_offline=row.cameras_offline or 0,
        )

    async def cleanup_old(
        self,
        db: AsyncSession,
        *,
        days: int = 30,
    ) -> int:
        """Delete activities older than N days. Returns count of deleted records."""
        cutoff = datetime.now() - timedelta(days=days)
        result = await db.execute(delete(Activity).where(Activity.timestamp < cutoff))
        await db.flush()
        return result.rowcount  # type: ignore[attr-defined, no-any-return]


activity_crud = CRUDActivity(Activity)
