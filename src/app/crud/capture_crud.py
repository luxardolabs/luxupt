"""CRUD operations for Capture model."""

from datetime import date

import config
from models.capture import Capture
from pydantic import BaseModel
from schemas.capture import CaptureCreate, CaptureStats
from sqlalchemy import Integer, and_, case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from crud.base import CRUDBase


class CaptureUpdate(BaseModel):
    """Placeholder for capture updates (rarely needed)."""

    pass


class CRUDCapture(CRUDBase[Capture, CaptureCreate, CaptureUpdate]):
    """CRUD operations for Capture model."""

    async def get_by_filters(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = config.DEFAULT_PAGE_SIZE,
    ) -> list[Capture]:
        """Get captures with optional filters."""
        query = select(Capture)

        if camera:
            query = query.where(Capture.camera_id == camera)
        if capture_date:
            query = query.where(Capture.capture_date == capture_date)
        if interval:
            query = query.where(Capture.interval == interval)
        if status:
            query = query.where(Capture.status == status)

        query = query.order_by(Capture.timestamp.desc()).offset(skip).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def count_by_filters(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
        status: str | None = None,
    ) -> int:
        """Count captures matching filters."""
        query = select(func.count(Capture.id))

        if camera:
            query = query.where(Capture.camera_id == camera)
        if capture_date:
            query = query.where(Capture.capture_date == capture_date)
        if interval:
            query = query.where(Capture.interval == interval)
        if status:
            query = query.where(Capture.status == status)

        result = await db.execute(query)
        return result.scalar() or 0

    async def get_latest_by_camera(
        self,
        db: AsyncSession,
        camera_id: str,
    ) -> Capture | None:
        """Get the latest successful capture for a camera."""
        result = await db.execute(
            select(Capture)
            .where(Capture.camera_id == camera_id, Capture.status == "success")
            .order_by(Capture.timestamp.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_per_camera(self, db: AsyncSession) -> dict[str, Capture]:
        """Get the latest successful capture for each camera in a single query.

        Uses a subquery to find max timestamp per camera, avoiding N+1 queries.
        Returns dict keyed by camera_id (UUID).
        """
        # Subquery: get max timestamp per camera for successful captures
        max_timestamp_subq = (
            select(
                Capture.camera_id,
                func.max(Capture.timestamp).label("max_ts"),
            )
            .where(Capture.status == "success")
            .group_by(Capture.camera_id)
            .subquery()
        )

        # Main query: join captures with subquery to get full records
        query = select(Capture).join(
            max_timestamp_subq,
            and_(
                Capture.camera_id == max_timestamp_subq.c.camera_id,
                Capture.timestamp == max_timestamp_subq.c.max_ts,
                Capture.status == "success",
            ),
        )

        result = await db.execute(query)
        captures = result.scalars().all()

        return {c.camera_id: c for c in captures}

    async def get_available_dates(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
    ) -> list[date]:
        """Get list of dates with captures."""
        query = select(Capture.capture_date).distinct()

        if camera:
            query = query.where(Capture.camera_id == camera)

        query = query.order_by(Capture.capture_date.desc())
        result = await db.execute(query)
        return [row[0] for row in result.fetchall()]

    async def get_available_cameras(self, db: AsyncSession) -> list[str]:
        """Get list of camera_ids with captures."""
        query = select(Capture.camera_id).distinct().order_by(Capture.camera_id)
        result = await db.execute(query)
        return [row[0] for row in result.fetchall()]

    async def get_available_intervals(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
    ) -> list[int]:
        """Get list of intervals used."""
        query = select(Capture.interval).distinct()

        if camera:
            query = query.where(Capture.camera_id == camera)
        if capture_date:
            query = query.where(Capture.capture_date == capture_date)

        query = query.order_by(Capture.interval)
        result = await db.execute(query)
        return [row[0] for row in result.fetchall()]

    async def get_stats(self, db: AsyncSession) -> CaptureStats:
        """Get overall capture statistics in a single query."""
        # Combine all aggregations into one query
        result = await db.execute(
            select(
                func.count(Capture.id).label("total"),
                func.sum(case((Capture.status == "success", 1), else_=0)).label("successful"),
                func.count(func.distinct(Capture.camera_id)).label("unique_cameras"),
                func.count(func.distinct(Capture.capture_date)).label("unique_dates"),
                func.coalesce(func.sum(Capture.file_size), 0).label("total_size"),
                func.min(Capture.capture_datetime).label("oldest"),
                func.max(Capture.capture_datetime).label("newest"),
            )
        )
        row = result.one()

        total = row.total or 0
        successful = row.successful or 0

        return CaptureStats(
            total_captures=total,
            successful_captures=successful,
            failed_captures=total - successful,
            unique_cameras=row.unique_cameras or 0,
            unique_dates=row.unique_dates or 0,
            total_file_size=row.total_size or 0,
            oldest_capture=row.oldest,
            newest_capture=row.newest,
        )

    async def get_captures_for_timelapse(
        self,
        db: AsyncSession,
        *,
        camera: str,
        capture_date: date,
        interval: int,
    ) -> list[Capture]:
        """Get all successful captures for timelapse creation."""
        result = await db.execute(
            select(Capture)
            .where(
                Capture.camera_id == camera,
                Capture.capture_date == capture_date,
                Capture.interval == interval,
                Capture.status == "success",
            )
            .order_by(Capture.timestamp.asc())
        )
        return list(result.scalars().all())

    async def delete_old_captures(
        self,
        db: AsyncSession,
        *,
        before_date: date,
    ) -> int:
        """Delete captures older than a date. Returns count of deleted records."""
        result = await db.execute(delete(Capture).where(Capture.capture_date < before_date))
        await db.flush()
        return result.rowcount  # type: ignore[attr-defined, no-any-return]

    async def delete_by_camera_date_interval(
        self,
        db: AsyncSession,
        *,
        camera: str,
        capture_date: date,
        interval: int,
    ) -> int:
        """Delete captures for a specific camera/date/interval. Returns count deleted."""
        result = await db.execute(
            delete(Capture).where(
                Capture.camera_id == camera,
                Capture.capture_date == capture_date,
                Capture.interval == interval,
            )
        )
        await db.flush()
        return result.rowcount  # type: ignore[attr-defined, no-any-return]

    async def get_recent_failures(
        self,
        db: AsyncSession,
        *,
        limit: int = config.RECENT_ITEMS_LIMIT,
    ) -> list[Capture]:
        """Get recent failed captures with error messages."""
        result = await db.execute(
            select(Capture).where(Capture.status == "failed").order_by(Capture.timestamp.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_camera_and_timestamp(
        self,
        db: AsyncSession,
        camera_id: str,
        timestamp: int,
        interval: int | None = None,
        *,
        status: str = "success",
    ) -> Capture | None:
        """Get a specific capture by camera and timestamp (and optionally interval).

        Args:
            status: Filter by status (default "success" to exclude failed captures)
        """
        query = select(Capture).where(
            Capture.camera_id == camera_id,
            Capture.timestamp == timestamp,
            Capture.status == status,
        )
        if interval:
            query = query.where(Capture.interval == interval)
        else:
            # If no interval specified, get the first one (sorted by interval)
            query = query.order_by(Capture.interval).limit(1)

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_adjacent_image(
        self,
        db: AsyncSession,
        *,
        camera_id: str,
        timestamp: int,
        current_id: int,
        direction: str,
        filter_camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
    ) -> Capture | None:
        """Get the previous or next image respecting filters.

        Args:
            current_id: ID of current image (for ordering when timestamps match)
            direction: "prev" for earlier timestamp, "next" for later timestamp
            filter_camera: If set, only navigate within this camera's images
            capture_date: If set, only navigate within this date
            interval: If set, only navigate within this interval
        """
        query = select(Capture).where(Capture.status == "success")

        # Apply filters - if filter_camera is set, use it; otherwise navigate across all
        if filter_camera:
            query = query.where(Capture.camera_id == filter_camera)
        if capture_date:
            query = query.where(Capture.capture_date == capture_date)
        if interval:
            query = query.where(Capture.interval == interval)

        # Navigate by timestamp + id to handle multiple cameras at same timestamp
        # Order: timestamp DESC, id DESC (newest first, matching dashboard display)
        if direction == "prev":
            # Previous = older OR same timestamp with lower id
            query = query.where(
                or_(Capture.timestamp < timestamp, and_(Capture.timestamp == timestamp, Capture.id < current_id))
            )
            query = query.order_by(Capture.timestamp.desc(), Capture.id.desc())
        else:  # next
            # Next = newer OR same timestamp with higher id
            query = query.where(
                or_(Capture.timestamp > timestamp, and_(Capture.timestamp == timestamp, Capture.id > current_id))
            )
            query = query.order_by(Capture.timestamp.asc(), Capture.id.asc())

        query = query.limit(1)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_time_series_stats(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        interval: int | None = None,
        since_timestamp: int | None = None,
        until_timestamp: int | None = None,
        bucket_seconds: int = 3600,
    ) -> list[dict]:
        """Get time series data for capture duration and file size, bucketed by time.

        Returns list of dicts with timestamp, avg_duration_ms, avg_file_size, count,
        and camera_breakdown (dict of camera -> {duration, size, count}).
        """
        filters = [Capture.status == "success"]
        if camera:
            filters.append(Capture.camera_id == camera)
        if interval:
            filters.append(Capture.interval == interval)
        if since_timestamp:
            filters.append(Capture.timestamp >= since_timestamp)
        if until_timestamp:
            filters.append(Capture.timestamp <= until_timestamp)

        # Group by time bucket and camera for breakdown
        bucket_expr = (Capture.timestamp / bucket_seconds).cast(Integer) * bucket_seconds

        query = (
            select(
                bucket_expr.label("bucket"),
                Capture.camera_id,
                func.avg(Capture.capture_duration_ms).label("avg_duration"),
                func.avg(Capture.file_size).label("avg_size"),
                func.count(Capture.id).label("count"),
            )
            .where(*filters)
            .group_by(bucket_expr, Capture.camera_id)
            .order_by(bucket_expr.asc())
        )

        result = await db.execute(query)
        rows = result.all()

        # Aggregate by bucket with camera breakdown
        buckets: dict[int, dict] = {}
        for row in rows:
            bucket_ts = int(row.bucket) * 1000  # Convert to JS milliseconds
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {
                    "timestamp": bucket_ts,
                    "total_duration": 0,
                    "total_size": 0,
                    "total_count": 0,
                    "cameras": {},
                }

            bucket = buckets[bucket_ts]
            row_count: int = row[4]  # Access count by index since 'count' conflicts with tuple.count
            bucket["total_duration"] += (row.avg_duration or 0) * row_count
            bucket["total_size"] += (row.avg_size or 0) * row_count
            bucket["total_count"] += row_count
            bucket["cameras"][row.camera_id] = {
                "duration_ms": round(row.avg_duration or 0, 1),
                "file_size": round((row.avg_size or 0) / 1024, 1),  # KB
                "count": row_count,
            }

        # Calculate weighted averages
        result_list = []
        for bucket in buckets.values():
            if bucket["total_count"] > 0:
                bucket["avg_duration_ms"] = round(bucket["total_duration"] / bucket["total_count"], 1)
                bucket["avg_file_size"] = round(bucket["total_size"] / bucket["total_count"] / 1024, 1)  # KB
            else:
                bucket["avg_duration_ms"] = 0
                bucket["avg_file_size"] = 0
            del bucket["total_duration"]
            del bucket["total_size"]
            result_list.append(bucket)

        return sorted(result_list, key=lambda x: x["timestamp"])

    async def get_avg_stats(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        interval: int | None = None,
        since_timestamp: int | None = None,
        until_timestamp: int | None = None,
    ) -> dict:
        """Get average duration and file size for captures."""
        query = select(
            func.avg(Capture.capture_duration_ms).label("avg_duration"),
            func.avg(Capture.file_size).label("avg_size"),
        ).where(Capture.status == "success")

        if camera:
            query = query.where(Capture.camera_id == camera)
        if interval:
            query = query.where(Capture.interval == interval)
        if since_timestamp:
            query = query.where(Capture.timestamp >= since_timestamp)
        if until_timestamp:
            query = query.where(Capture.timestamp <= until_timestamp)

        result = await db.execute(query)
        row = result.one()

        return {
            "avg_duration_ms": row.avg_duration or 0,
            "avg_file_size": row.avg_size or 0,
        }

    async def get_camera_breakdown(
        self,
        db: AsyncSession,
        *,
        interval: int | None = None,
        since_timestamp: int | None = None,
        until_timestamp: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Get capture count breakdown by camera.

        Returns list of dicts with camera (safe_name for display), camera_id, count.
        """
        # Build filters first
        filters = [Capture.status == "success"]
        if interval:
            filters.append(Capture.interval == interval)
        if since_timestamp:
            filters.append(Capture.timestamp >= since_timestamp)
        if until_timestamp:
            filters.append(Capture.timestamp <= until_timestamp)

        query = (
            select(
                Capture.camera_id,
                Capture.camera_safe_name,
                func.count(Capture.id).label("count"),
            )
            .where(*filters)
            .group_by(Capture.camera_id, Capture.camera_safe_name)
            .order_by(func.count(Capture.id).desc())
            .limit(limit)
        )

        result = await db.execute(query)
        rows = result.all()

        return [{"camera": row.camera_safe_name, "camera_id": row.camera_id, "count": row.count} for row in rows]

    async def get_success_failure_stats(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        interval: int | None = None,
        since_timestamp: int | None = None,
        until_timestamp: int | None = None,
    ) -> dict:
        """Get success/failure counts and percentages for captures."""
        filters = []
        if camera:
            filters.append(Capture.camera_id == camera)
        if interval:
            filters.append(Capture.interval == interval)
        if since_timestamp:
            filters.append(Capture.timestamp >= since_timestamp)
        if until_timestamp:
            filters.append(Capture.timestamp <= until_timestamp)

        # Get total count
        total_query = select(func.count(Capture.id))
        if filters:
            total_query = total_query.where(*filters)
        total_result = await db.execute(total_query)
        total = total_result.scalar() or 0

        # Get success count
        success_query = select(func.count(Capture.id)).where(Capture.status == "success")
        if filters:
            success_query = success_query.where(*filters)
        success_result = await db.execute(success_query)
        success = success_result.scalar() or 0

        failed = total - success
        success_pct = round((success / total * 100), 1) if total > 0 else 0
        failed_pct = round((failed / total * 100), 1) if total > 0 else 0

        return {
            "total": total,
            "success": success,
            "failed": failed,
            "success_pct": success_pct,
            "failed_pct": failed_pct,
        }

    async def get_success_failure_timeseries(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        interval: int | None = None,
        since_timestamp: int | None = None,
        until_timestamp: int | None = None,
        bucket_seconds: int = 3600,  # 1 hour buckets by default
    ) -> list[dict]:
        """Get success/failure counts over time in buckets.

        Returns list of dicts with timestamp, success_count, failure_count.
        """
        filters = []
        if camera:
            filters.append(Capture.camera_id == camera)
        if interval:
            filters.append(Capture.interval == interval)
        if since_timestamp:
            filters.append(Capture.timestamp >= since_timestamp)
        if until_timestamp:
            filters.append(Capture.timestamp <= until_timestamp)

        # Group by time bucket using integer division
        bucket_expr = (Capture.timestamp / bucket_seconds).cast(Integer) * bucket_seconds

        query = (
            select(
                bucket_expr.label("bucket"),
                func.sum(case((Capture.status == "success", 1), else_=0)).label("success"),
                func.sum(case((Capture.status != "success", 1), else_=0)).label("failed"),
            )
            .group_by(bucket_expr)
            .order_by(bucket_expr.asc())
        )

        if filters:
            query = query.where(*filters)

        result = await db.execute(query)
        rows = result.all()

        return [
            {
                "timestamp": int(row.bucket) * 1000,  # Convert to JS milliseconds
                "success": row.success or 0,
                "failed": row.failed or 0,
            }
            for row in rows
        ]

    async def get_deletion_preview(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
    ) -> list[dict]:
        """Get summary of captures that would be deleted, grouped by camera/date/interval.

        Args:
            camera: camera_id (UUID) to filter by.

        Returns list of dicts with camera (safe_name for paths), camera_id, date, interval, count, total_size.
        """
        query = select(
            Capture.camera_id,
            Capture.camera_safe_name,
            Capture.capture_date,
            Capture.interval,
            func.count(Capture.id).label("count"),
            func.coalesce(func.sum(Capture.file_size), 0).label("total_size"),
        )

        if camera:
            query = query.where(Capture.camera_id == camera)
        if capture_date:
            query = query.where(Capture.capture_date == capture_date)
        if interval:
            query = query.where(Capture.interval == interval)

        query = query.group_by(
            Capture.camera_id,
            Capture.camera_safe_name,
            Capture.capture_date,
            Capture.interval,
        ).order_by(
            Capture.capture_date.desc(),
            Capture.camera_safe_name,
            Capture.interval,
        )

        result = await db.execute(query)
        rows = result.all()

        return [
            {
                "camera": row.camera_safe_name,  # safe_name for file paths
                "camera_id": row.camera_id,
                "date": row.capture_date,
                "interval": row.interval,
                "count": row.count,
                "total_size": row.total_size,
            }
            for row in rows
        ]

    async def get_file_paths_by_filters(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
    ) -> list[dict]:
        """Get file paths and cleanup metadata for captures matching filters.

        Args:
            camera: camera_id (UUID) to filter by.

        Returns minimal dicts with: camera (safe_name), date, interval, file_path.
        """
        query = select(
            Capture.camera_safe_name,
            Capture.capture_date,
            Capture.interval,
            Capture.file_path,
        )

        if camera:
            query = query.where(Capture.camera_id == camera)
        if capture_date:
            query = query.where(Capture.capture_date == capture_date)
        if interval:
            query = query.where(Capture.interval == interval)

        result = await db.execute(query)
        return [
            {
                "camera": row.camera_safe_name,
                "date": row.capture_date,
                "interval": row.interval,
                "file_path": row.file_path,
            }
            for row in result.all()
        ]

    async def bulk_delete_by_filters(
        self,
        db: AsyncSession,
        *,
        camera: str | None = None,
        capture_date: date | None = None,
        interval: int | None = None,
    ) -> int:
        """Bulk delete captures matching filters in a single SQL DELETE.

        Args:
            camera: camera_id (UUID) to filter by.

        Returns count deleted.
        """
        query = delete(Capture)

        if camera:
            query = query.where(Capture.camera_id == camera)
        if capture_date:
            query = query.where(Capture.capture_date == capture_date)
        if interval:
            query = query.where(Capture.interval == interval)

        result = await db.execute(query)
        await db.flush()
        return result.rowcount  # type: ignore[attr-defined, no-any-return]


capture_crud = CRUDCapture(Capture)
