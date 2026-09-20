"""CRUD operations for Camera model."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base_crud import CRUDBase
from app.models.camera_model import Camera
from app.models.capture_model import Capture
from app.models.timelapse_model import Timelapse
from app.schemas.camera_schema import CameraCreate, CameraUpdate
from app.utils.timezones import business_day


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
        result = await db.execute(
            select(Camera).where(Camera.is_active.is_(True)).order_by(Camera.name)
        )
        return list(result.scalars().all())

    async def get_inactive(self, db: AsyncSession) -> list[Camera]:
        """Get all inactive (disabled) cameras."""
        result = await db.execute(
            select(Camera).where(Camera.is_active.is_(False)).order_by(Camera.name)
        )
        return list(result.scalars().all())

    async def get_connected(self, db: AsyncSession) -> list[Camera]:
        """Get all connected cameras."""
        result = await db.execute(
            select(Camera)
            .where(Camera.is_active.is_(True), Camera.is_connected == True)  # noqa: E712
            .order_by(Camera.name)
        )
        return list(result.scalars().all())

    async def upsert_from_dict(
        self, db: AsyncSession, *, data: dict[str, Any]
    ) -> Camera:
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
                update_data["last_seen_at"] = datetime.now(UTC)
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
        camera.last_capture_at = datetime.now(UTC)

        db.add(camera)
        await db.flush()
        await db.refresh(camera)
        return camera

    async def get_camera_stats(
        self,
        db: AsyncSession,
        camera_id: str,
        *,
        global_intervals: list[int] | None = None,
    ) -> dict[str, Any]:
        """Get statistics for a camera."""
        camera = await self.get_by_camera_id(db, camera_id)
        if not camera:
            return {}
        stats = await self.get_camera_stats_bulk(
            db, [camera], global_intervals=global_intervals
        )
        return stats.get(camera.camera_id, {})

    async def get_camera_stats_bulk(
        self,
        db: AsyncSession,
        cameras: list[Camera],
        *,
        global_intervals: list[int] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Get statistics for many cameras in a fixed number of queries, keyed by camera_id.

        The per-camera form issued SIX queries each, so rendering the cameras page — which
        shows a card per camera — cost 6xN round-trips against a captures table with hundreds
        of thousands of rows. This does the same work in four, by grouping on ``camera_id``
        instead of filtering to one.

        ``get_camera_stats`` delegates here rather than keeping its own copy of the counting
        and expected-vs-actual arithmetic, so the single- and many-camera answers cannot
        drift apart.
        """
        if not cameras:
            return {}

        ids = [c.camera_id for c in cameras]

        # 1. capture totals + failures, per camera
        totals_rows = (
            await db.execute(
                select(
                    Capture.camera_id,
                    func.count(Capture.id).label("total"),
                    func.sum(case((Capture.status == "failed", 1), else_=0)).label(
                        "failed"
                    ),
                    func.count(func.distinct(Capture.capture_date)).label("days"),
                )
                .where(Capture.camera_id.in_(ids))
                .group_by(Capture.camera_id)
            )
        ).fetchall()
        totals = {r.camera_id: r for r in totals_rows}

        # 2. timelapse counts, per camera
        tl_rows = (
            await db.execute(
                select(Timelapse.camera_id, func.count(Timelapse.id).label("n"))
                .where(Timelapse.camera_id.in_(ids))
                .group_by(Timelapse.camera_id)
            )
        ).fetchall()
        timelapse_counts = {r.camera_id: int(r.n or 0) for r in tl_rows}

        # 3. today's captures by (camera, interval)
        # Business day: `Capture.capture_date` is the local calendar day the frame was
        # filed under, so "today's captures" must use the same boundary.
        today = business_day()
        interval_rows = (
            await db.execute(
                select(
                    Capture.camera_id,
                    Capture.interval,
                    func.sum(case((Capture.status == "success", 1), else_=0)).label(
                        "success"
                    ),
                    func.sum(case((Capture.status != "success", 1), else_=0)).label(
                        "failed"
                    ),
                    func.min(Capture.timestamp).label("first_capture_ts"),
                )
                .where(
                    Capture.camera_id.in_(ids),
                    Capture.capture_date == today,
                )
                .group_by(Capture.camera_id, Capture.interval)
                .order_by(Capture.camera_id, Capture.interval)
            )
        ).fetchall()

        now_ts = int(datetime.now(UTC).timestamp())
        per_camera_intervals: dict[str, dict[int, dict[str, Any]]] = {}
        for row in interval_rows:
            if row.first_capture_ts and row.interval > 0:
                elapsed = max(0, now_ts - int(row.first_capture_ts))
                # +1 includes the first capture itself
                expected = int(elapsed / row.interval) + 1
            else:
                expected = 0
            success = int(row.success or 0)
            failed_count = int(row.failed or 0)
            rate = round(success / expected * 100, 1) if expected > 0 else 0.0
            per_camera_intervals.setdefault(row.camera_id, {})[row.interval] = {
                "success": success,
                "failed": failed_count,
                "expected": expected,
                "rate": rate,
            }

        stats: dict[str, dict[str, Any]] = {}
        for camera in cameras:
            row = totals.get(camera.camera_id)
            total = int(row.total or 0) if row else 0
            failed = int(row.failed or 0) if row else 0
            capture_days = int(row.days or 0) if row else 0
            success_rate = ((total - failed) / total * 100) if total > 0 else 0.0

            interval_stats = per_camera_intervals.get(camera.camera_id, {})
            # Include intervals the camera is configured for but has no captures on today
            effective_intervals = camera.enabled_intervals or global_intervals or []
            for iv in effective_intervals:
                if iv not in interval_stats:
                    interval_stats[iv] = {
                        "success": 0,
                        "failed": 0,
                        "expected": 0,
                        "rate": 0.0,
                    }
            interval_stats = dict(sorted(interval_stats.items()))

            total_success_today = sum(d["success"] for d in interval_stats.values())
            total_failed_today = sum(d["failed"] for d in interval_stats.values())
            total_expected_today = sum(d["expected"] for d in interval_stats.values())
            overall_rate = (
                round(total_success_today / total_expected_today * 100, 1)
                if total_expected_today > 0
                else 0.0
            )

            stats[camera.camera_id] = {
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
                "timelapse_count": timelapse_counts.get(camera.camera_id, 0),
                "capture_days": capture_days,
                "interval_stats": interval_stats,
                "today_summary": {
                    "success": total_success_today,
                    "failed": total_failed_today,
                    "expected": total_expected_today,
                    "rate": overall_rate,
                },
            }
        return stats

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

        update_data: dict[str, Any] = {}
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
        result = await db.execute(
            select(Camera).where(Camera.is_active.is_(True)).order_by(Camera.name)
        )
        cameras = list(result.scalars().all())

        # Filter by interval
        return [
            cam
            for cam in cameras
            if cam.enabled_intervals is None or interval in cam.enabled_intervals
        ]

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
            camera.first_discovered_at = datetime.now(UTC)
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
