"""CRUD operations for Timelapse model."""

from datetime import date

import config
from models.timelapse import Timelapse
from schemas.timelapse import TimelapseCreate, TimelapseStats, TimelapseUpdate
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crud.base import CRUDBase


class CRUDTimelapse(CRUDBase[Timelapse, TimelapseCreate, TimelapseUpdate]):
    """CRUD operations for Timelapse model."""

    async def get_by_filters(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        timelapse_date: date | None = None,
        interval: int | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = config.DEFAULT_PAGE_SIZE,
    ) -> list[Timelapse]:
        """Get timelapses with optional filters."""
        query = select(Timelapse)

        if camera:
            query = query.where(Timelapse.camera_id == camera)
        if timelapse_date:
            query = query.where(Timelapse.timelapse_date == timelapse_date)
        if interval:
            query = query.where(Timelapse.interval == interval)
        if status:
            query = query.where(Timelapse.status == status)

        query = query.order_by(Timelapse.timelapse_date.desc(), Timelapse.created_at.desc())
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def count_by_filters(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        timelapse_date: date | None = None,
        interval: int | None = None,
        status: str | None = None,
    ) -> int:
        """Count timelapses matching filters."""
        query = select(func.count(Timelapse.id))

        if camera:
            query = query.where(Timelapse.camera_id == camera)
        if timelapse_date:
            query = query.where(Timelapse.timelapse_date == timelapse_date)
        if interval:
            query = query.where(Timelapse.interval == interval)
        if status:
            query = query.where(Timelapse.status == status)

        result = await db.execute(query)
        return result.scalar() or 0

    async def get_by_camera_date_interval(
        self,
        db: AsyncSession,
        *,
        camera: str,
        timelapse_date: date,
        interval: int,
    ) -> Timelapse | None:
        """Get a specific timelapse by camera, date, and interval."""
        result = await db.execute(
            select(Timelapse).where(
                Timelapse.camera_id == camera,
                Timelapse.timelapse_date == timelapse_date,
                Timelapse.interval == interval,
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_by_camera(
        self,
        db: AsyncSession,
        camera: str,
    ) -> Timelapse | None:
        """Get the latest completed timelapse for a camera."""
        result = await db.execute(
            select(Timelapse)
            .where(Timelapse.camera_id == camera, Timelapse.status == "completed")
            .order_by(Timelapse.timelapse_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_available_dates(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        status: str | None = None,
    ) -> list[date]:
        """Get list of dates with timelapses."""
        query = select(Timelapse.timelapse_date).distinct()

        if camera:
            query = query.where(Timelapse.camera_id == camera)
        if status:
            query = query.where(Timelapse.status == status)

        query = query.order_by(Timelapse.timelapse_date.desc())
        result = await db.execute(query)
        return [row[0] for row in result.fetchall()]

    async def get_available_cameras(
        self,
        db: AsyncSession,
        *,
        status: str | None = None,
    ) -> list[str]:
        """Get list of cameras with timelapses."""
        query = select(Timelapse.camera_id).distinct()

        # Filter out empty/null camera_ids (legacy data)
        query = query.where(Timelapse.camera_id.isnot(None), Timelapse.camera_id != "")

        if status:
            query = query.where(Timelapse.status == status)

        query = query.order_by(Timelapse.camera_id)
        result = await db.execute(query)
        return [row[0] for row in result.fetchall()]

    async def get_available_intervals(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        status: str | None = None,
    ) -> list[int]:
        """Get list of intervals with timelapses."""
        query = select(Timelapse.interval).distinct()

        if camera:
            query = query.where(Timelapse.camera_id == camera)
        if status:
            query = query.where(Timelapse.status == status)

        query = query.order_by(Timelapse.interval)
        result = await db.execute(query)
        return [row[0] for row in result.fetchall()]

    async def get_stats(self, db: AsyncSession) -> TimelapseStats:
        """Get overall timelapse statistics in a single query."""
        result = await db.execute(
            select(
                func.count(Timelapse.id).label("total"),
                func.sum(case((Timelapse.status == "completed", 1), else_=0)).label("completed"),
                func.sum(case((Timelapse.status == "pending", 1), else_=0)).label("pending"),
                func.sum(case((Timelapse.status == "failed", 1), else_=0)).label("failed"),
                func.coalesce(
                    func.sum(case((Timelapse.status == "completed", Timelapse.duration_seconds), else_=0)), 0
                ).label("total_duration"),
                func.coalesce(func.sum(case((Timelapse.status == "completed", Timelapse.file_size), else_=0)), 0).label(
                    "total_size"
                ),
            )
        )
        row = result.one()

        return TimelapseStats(
            total_timelapses=row.total or 0,
            completed_timelapses=row.completed or 0,
            pending_timelapses=row.pending or 0,
            failed_timelapses=row.failed or 0,
            total_duration_seconds=row.total_duration or 0.0,
            total_file_size=row.total_size or 0,
        )


timelapse_crud = CRUDTimelapse(Timelapse)
