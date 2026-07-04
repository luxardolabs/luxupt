"""CRUD operations for Camera model."""

from datetime import date, datetime
from typing import Any

from models.camera import Camera
from models.capture import Capture
from models.timelapse import Timelapse
from schemas.camera import CameraCreate, CameraUpdate
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crud.base import CRUDBase


class CRUDCamera(CRUDBase[Camera, CameraCreate, CameraUpdate]):
    """CRUD operations for Camera model."""

    async def get_by_camera_id(self, db: AsyncSession, camera_id: str) -> Camera | None:
        """Get a camera by its UniFi camera ID."""
        result = await db.execute(select(Camera).where(Camera.camera_id == camera_id))
        return result.scalar_one_or_none()

    async def get_by_safe_name(self, db: AsyncSession, safe_name: str) -> Camera | None:
        """Get a camera by its safe name."""
        result = await db.execute(select(Camera).where(Camera.safe_name == safe_name))
        return result.scalar_one_or_none()

    async def get_active(self, db: AsyncSession) -> list[Camera]:
        """Get all active cameras."""
        result = await db.execute(select(Camera).where(Camera.is_active == True).order_by(Camera.name))  # noqa: E712
        return list(result.scalars().all())

    async def get_inactive(self, db: AsyncSession) -> list[Camera]:
        """Get all inactive (disabled) cameras."""
        result = await db.execute(select(Camera).where(Camera.is_active == False).order_by(Camera.name))  # noqa: E712
        return list(result.scalars().all())

    async def get_connected(self, db: AsyncSession) -> list[Camera]:
        """Get all connected cameras."""
        result = await db.execute(
            select(Camera)
            .where(Camera.is_active == True, Camera.is_connected == True)  # noqa: E712
            .order_by(Camera.name)
        )
        return list(result.scalars().all())

    async def upsert(self, db: AsyncSession, *, obj_in: CameraCreate) -> Camera:
        """Create or update a camera by camera_id."""
        existing = await self.get_by_camera_id(db, obj_in.camera_id)
        if existing:
            return await self.update(db, db_obj=existing, obj_in=obj_in.model_dump())
        return await self.create(db, obj_in=obj_in)

    async def upsert_from_dict(self, db: AsyncSession, *, data: dict) -> Camera:
        """Create or update a camera from a dictionary."""
        camera_id = data.get("camera_id")
        if not camera_id:
            raise ValueError("camera_id is required")

        existing = await self.get_by_camera_id(db, camera_id)
        if existing:
            return await self.update(db, db_obj=existing, obj_in=data)
        return await self.create_from_dict(db, data=data)

    async def update_status(
        self,
        db: AsyncSession,
        camera_id: str,
        *,
        is_connected: bool | None = None,
        is_recording: bool | None = None,
        state: str | None = None,
    ) -> Camera | None:
        """Update camera status fields."""
        camera = await self.get_by_camera_id(db, camera_id)
        if not camera:
            return None

        update_data: dict[str, Any] = {}
        if is_connected is not None:
            update_data["is_connected"] = is_connected
            if is_connected:
                update_data["last_seen_at"] = datetime.now()
        if is_recording is not None:
            update_data["is_recording"] = is_recording
        if state is not None:
            update_data["state"] = state

        if update_data:
            return await self.update(db, db_obj=camera, obj_in=update_data)
        return camera

    async def increment_captures(
        self,
        db: AsyncSession,
        camera_id: str,
        *,
        success: bool = True,
    ) -> Camera | None:
        """Increment capture counts for a camera."""
        camera = await self.get_by_camera_id(db, camera_id)
        if not camera:
            return None

        camera.total_captures += 1
        if not success:
            camera.failed_captures += 1
        camera.last_capture_at = datetime.now()

        db.add(camera)
        await db.flush()
        await db.refresh(camera)
        return camera

    async def get_camera_stats(
        self, db: AsyncSession, camera_id: str, *, global_intervals: list[int] | None = None
    ) -> dict:
        """Get statistics for a camera."""
        camera = await self.get_by_camera_id(db, camera_id)
        if not camera:
            return {}

        # Get actual counts from captures table
        total_result = await db.execute(
            select(func.count(Capture.id)).where(
                Capture.camera_id == camera.camera_id,
            )
        )
        total = total_result.scalar() or 0

        failed_result = await db.execute(
            select(func.count(Capture.id)).where(
                Capture.camera_id == camera.camera_id,
                Capture.status == "failed",
            )
        )
        failed = failed_result.scalar() or 0

        success_rate = ((total - failed) / total * 100) if total > 0 else 0.0

        # Get timelapse count
        timelapse_result = await db.execute(
            select(func.count(Timelapse.id)).where(
                Timelapse.camera_id == camera.camera_id,
            )
        )
        timelapse_count = timelapse_result.scalar() or 0

        # Get distinct capture days
        days_result = await db.execute(
            select(func.count(func.distinct(Capture.capture_date))).where(
                Capture.camera_id == camera.camera_id,
            )
        )
        capture_days = days_result.scalar() or 0

        # Get per-interval stats (today's captures by interval — success + failed)
        today = date.today()
        interval_stats_result = await db.execute(
            select(
                Capture.interval,
                func.count(Capture.id).label("total"),
                func.sum(case((Capture.status == "success", 1), else_=0)).label("success"),
                func.sum(case((Capture.status != "success", 1), else_=0)).label("failed"),
                func.min(Capture.timestamp).label("first_capture_ts"),
            )
            .where(
                Capture.camera_id == camera.camera_id,
                Capture.capture_date == today,
            )
            .group_by(Capture.interval)
            .order_by(Capture.interval)
        )
        rows = interval_stats_result.fetchall()

        # Calculate expected captures per interval from first capture of the day
        now_ts = int(datetime.now().timestamp())

        # Build interval_stats dict from query results
        interval_stats: dict[int, dict] = {}
        for row in rows:
            if row.first_capture_ts and row.interval > 0:
                elapsed = max(0, now_ts - int(row.first_capture_ts))
                expected = int(elapsed / row.interval) + 1  # +1 includes the first capture itself
            else:
                expected = 0
            success = int(row.success or 0)
            failed_count = int(row.failed or 0)
            rate = round(success / expected * 100, 1) if expected > 0 else 0.0
            interval_stats[row.interval] = {
                "success": success,
                "failed": failed_count,
                "expected": expected,
                "rate": rate,
            }

        # Determine effective intervals for this camera and include zero-capture intervals
        effective_intervals = camera.enabled_intervals or global_intervals or []
        for iv in effective_intervals:
            if iv not in interval_stats:
                interval_stats[iv] = {"success": 0, "failed": 0, "expected": 0, "rate": 0.0}

        # Compute today_summary across all intervals
        total_success_today = sum(d["success"] for d in interval_stats.values())
        total_failed_today = sum(d["failed"] for d in interval_stats.values())
        total_expected_today = sum(d["expected"] for d in interval_stats.values())
        overall_rate = round(total_success_today / total_expected_today * 100, 1) if total_expected_today > 0 else 0.0
        today_summary = {
            "success": total_success_today,
            "failed": total_failed_today,
            "expected": total_expected_today,
            "rate": overall_rate,
        }

        return {
            "camera_id": camera.camera_id,
            "name": camera.name,
            "safe_name": camera.safe_name,
            "total_captures": total,
            "successful_captures": total - failed,
            "failed_captures": failed,
            "success_rate": round(success_rate, 2),
            "last_capture_at": camera.last_capture_at,
            "is_connected": camera.is_connected,
            "captures_today": total_success_today + total_failed_today,
            "timelapse_count": timelapse_count,
            "capture_days": capture_days,
            "interval_stats": interval_stats,
            "today_summary": today_summary,
        }

    async def update_capture_settings(
        self,
        db: AsyncSession,
        camera_id: str,
        *,
        capture_method: str | None = None,
        rtsp_quality: str | None = None,
        enabled_intervals: list[int] | None = None,
    ) -> Camera | None:
        """Update per-camera capture settings."""
        camera = await self.get_by_camera_id(db, camera_id)
        if not camera:
            return None

        update_data: dict = {}
        if capture_method is not None:
            update_data["capture_method"] = capture_method
        if rtsp_quality is not None:
            update_data["rtsp_quality"] = rtsp_quality
        if enabled_intervals is not None:
            update_data["enabled_intervals"] = enabled_intervals

        if update_data:
            return await self.update(db, db_obj=camera, obj_in=update_data)
        return camera

    async def update_capability_detection(
        self,
        db: AsyncSession,
        camera_id: str,
        *,
        api_max_resolution: str | None = None,
        rtsp_max_resolution: str | None = None,
        recommended_method: str | None = None,
    ) -> Camera | None:
        """Update capability detection results for a camera."""
        camera = await self.get_by_camera_id(db, camera_id)
        if not camera:
            return None

        update_data: dict = {}
        if api_max_resolution is not None:
            update_data["api_max_resolution"] = api_max_resolution
        if rtsp_max_resolution is not None:
            update_data["rtsp_max_resolution"] = rtsp_max_resolution
        if recommended_method is not None:
            update_data["recommended_method"] = recommended_method

        if update_data:
            return await self.update(db, db_obj=camera, obj_in=update_data)
        return camera

    async def get_cameras_for_interval(
        self,
        db: AsyncSession,
        interval: int,
    ) -> list[Camera]:
        """Get all active cameras that have the specified interval enabled.

        A camera has an interval enabled if:
        - enabled_intervals is None (use all intervals), OR
        - the interval is in the enabled_intervals list
        """
        result = await db.execute(select(Camera).where(Camera.is_active == True).order_by(Camera.name))  # noqa: E712
        cameras = list(result.scalars().all())

        # Filter by interval
        return [cam for cam in cameras if cam.enabled_intervals is None or interval in cam.enabled_intervals]

    async def set_first_discovered(
        self,
        db: AsyncSession,
        camera_id: str,
    ) -> Camera | None:
        """Set the first_discovered_at timestamp if not already set."""
        camera = await self.get_by_camera_id(db, camera_id)
        if not camera:
            return None

        if camera.first_discovered_at is None:
            camera.first_discovered_at = datetime.now()
            db.add(camera)
            await db.flush()
            await db.refresh(camera)

        return camera

    async def delete_by_camera_id(self, db: AsyncSession, camera_id: str) -> bool:
        """Delete a camera by its UniFi camera ID.

        Related captures and timelapses will have their camera_db_id set to NULL
        due to the ondelete="SET NULL" FK constraint. Files on disk are NOT deleted.

        Returns True if camera was deleted, False if not found.
        """
        camera = await self.get_by_camera_id(db, camera_id)
        if not camera:
            return False

        await db.delete(camera)
        await db.flush()
        return True


camera_crud = CRUDCamera(Camera)
