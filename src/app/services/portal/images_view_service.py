"""Images view service for preparing image browser template data."""

from datetime import date

from services.camera_service import CameraService
from services.capture_cleanup_service import CaptureCleanupService
from services.capture_service import CaptureService


class ImagesViewService:
    """Prepares data for image browsing pages."""

    def __init__(
        self,
        camera_service: CameraService,
        capture_service: CaptureService,
        cleanup_service: CaptureCleanupService,
    ):
        """Initialize with core services."""
        self.camera_service = camera_service
        self.capture_service = capture_service
        self.cleanup_service = cleanup_service

    async def get_capture_path(
        self, camera_safe_name: str, timestamp: int, interval: int | None = None
    ) -> tuple[str | None, bool]:
        """Get capture file path with path traversal protection.

        Args:
            camera_safe_name: Camera safe name (from URL path)
            timestamp: Capture timestamp
            interval: Capture interval (required to disambiguate when same timestamp exists at multiple intervals)

        Returns:
            Tuple of (file_path, exists_on_disk)
        """
        # Lookup camera to get camera_id for queries
        camera = await self.camera_service.get_by_safe_name(camera_safe_name)
        if not camera:
            return None, False

        return await self.capture_service.get_validated_file_path(camera.camera_id, timestamp, interval)

    async def get_capture_for_thumbnail(
        self,
        camera_safe_name: str,
        interval: int,
        timestamp: int,
    ) -> dict | None:
        """Get capture info for thumbnail generation.

        Args:
            camera_safe_name: Camera safe name (from URL path)
            interval: Capture interval (required - must match exactly)
            timestamp: Capture timestamp

        Returns:
            Dict with file_path, camera_safe_name, interval, capture_date or None if not found
        """
        # Lookup camera to get camera_id for queries
        camera = await self.camera_service.get_by_safe_name(camera_safe_name)
        if not camera:
            return None

        camera_id = camera.camera_id

        # Get capture with exact interval match
        capture = await self.capture_service.get_by_camera_and_timestamp(camera_id, timestamp, interval=interval)
        if not capture:
            return None

        # Validate file path
        file_path, exists = await self.capture_service.get_validated_file_path(camera_id, timestamp)
        if not file_path:
            return None

        return {
            "file_path": file_path,
            "camera_safe_name": capture.camera_safe_name,
            "interval": capture.interval,
            "capture_date": capture.capture_date,
        }

    async def get_browser_context(
        self,
        *,
        camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> dict:
        """Get all data needed for images browser page."""
        # Parse date string if provided
        capture_date = date.fromisoformat(date_str) if date_str else None

        # Get total count for pagination
        total = await self.capture_service.count_by_filters(
            camera=camera,
            capture_date=capture_date,
            interval=interval,
            status="success",
        )

        # Get images with filters and pagination
        skip = (page - 1) * per_page
        images = await self.capture_service.get_by_filters(
            camera=camera,
            capture_date=capture_date,
            interval=interval,
            status="success",
            skip=skip,
            limit=per_page,
        )

        # Get filter options
        cameras = await self.camera_service.get_active()
        available_dates = await self.capture_service.get_available_dates(camera=camera)
        available_intervals = await self.capture_service.get_available_intervals(camera=camera)

        # Get stats for total storage size
        capture_stats = await self.capture_service.get_capture_stats()
        total_size_gb = round(capture_stats.total_file_size / 1024 / 1024 / 1024, 2)

        # Calculate pagination
        total_pages = (total + per_page - 1) // per_page if total > 0 else 1

        return {
            "images": images,
            "cameras": cameras,
            "available_dates": available_dates,
            "available_intervals": available_intervals,
            "total_size_gb": total_size_gb,
            "filters": {
                "camera": camera,
                "date": capture_date,
                "interval": interval,
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "total_count": total,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        }

    async def get_image_grid_context(
        self,
        *,
        camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> dict:
        """Get data for image grid partial (HTMX)."""
        # Parse date string if provided
        capture_date = date.fromisoformat(date_str) if date_str else None

        # Get total count for pagination
        total = await self.capture_service.count_by_filters(
            camera=camera,
            capture_date=capture_date,
            interval=interval,
            status="success",
        )

        # Get images with filters and pagination
        skip = (page - 1) * per_page
        images = await self.capture_service.get_by_filters(
            camera=camera,
            capture_date=capture_date,
            interval=interval,
            status="success",
            skip=skip,
            limit=per_page,
        )

        total_pages = (total + per_page - 1) // per_page if total > 0 else 1

        return {
            "images": images,
            "filters": {
                "camera": camera,
                "date": capture_date,
                "interval": interval,
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "total_count": total,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        }

    async def get_camera_images_context(
        self,
        camera_safe_name: str,
        *,
        date_str: str | None = None,
        interval: int | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        """Get images for a specific camera."""
        # Parse date string if provided
        capture_date = date.fromisoformat(date_str) if date_str else None

        camera = await self.camera_service.get_by_safe_name(camera_safe_name)

        if not camera:
            return {
                "camera": None,
                "images": [],
                "pagination": {"page": 1, "total_pages": 1, "total_count": 0},
            }

        # Use camera_id for queries
        camera_id = camera.camera_id

        total = await self.capture_service.count_by_filters(
            camera=camera_id,
            capture_date=capture_date,
            interval=interval,
            status="success",
        )

        skip = (page - 1) * per_page
        images = await self.capture_service.get_by_filters(
            camera=camera_id,
            capture_date=capture_date,
            interval=interval,
            status="success",
            skip=skip,
            limit=per_page,
        )

        available_dates = await self.capture_service.get_available_dates(camera=camera_id)
        available_intervals = await self.capture_service.get_available_intervals(camera=camera_id)

        total_pages = (total + per_page - 1) // per_page if total > 0 else 1

        return {
            "camera": camera,
            "images": images,
            "available_dates": available_dates,
            "available_intervals": available_intervals,
            "filters": {
                "date": capture_date,
                "interval": interval,
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "total_count": total,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        }

    async def get_latest_images_context(self) -> dict:
        """Get latest images for each camera."""
        latest_captures = await self.capture_service.get_latest_captures_all()
        cameras = await self.camera_service.get_active()

        return {
            "latest_captures": latest_captures,
            "cameras": cameras,
        }

    async def get_lightbox_context(
        self,
        camera_safe_name: str,
        timestamp: int,
        *,
        filter_camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
    ) -> dict:
        """Get lightbox context with current image and prev/next for navigation."""
        # Parse date string if provided
        capture_date = date.fromisoformat(date_str) if date_str else None

        # Lookup camera to get camera_id for queries
        camera = await self.camera_service.get_by_safe_name(camera_safe_name)
        if not camera:
            return {"image": None, "prev_image": None, "next_image": None}

        camera_id = camera.camera_id

        # Get the current image (include interval to handle same-timestamp different-interval captures)
        image = await self.capture_service.get_by_camera_and_timestamp(
            camera_id,
            timestamp,
            interval=interval,
        )

        if not image:
            return {"image": None, "prev_image": None, "next_image": None}

        # Get prev/next images respecting filters
        prev_image = await self.capture_service.get_adjacent_image(
            camera_id=camera_id,
            timestamp=timestamp,
            current_id=image.id,
            direction="prev",
            filter_camera=filter_camera,
            capture_date=capture_date,
            interval=interval,
        )

        next_image = await self.capture_service.get_adjacent_image(
            camera_id=camera_id,
            timestamp=timestamp,
            current_id=image.id,
            direction="next",
            filter_camera=filter_camera,
            capture_date=capture_date,
            interval=interval,
        )

        return {
            "image": image,
            "prev_image": prev_image,
            "next_image": next_image,
        }

    async def get_delete_panel_context(self) -> dict:
        """Get context for the image deletion panel including initial preview."""
        camera_ids = await self.capture_service.get_available_cameras()
        dates = await self.capture_service.get_available_dates()
        intervals = await self.capture_service.get_available_intervals()

        # Get camera objects for display names
        all_cameras = await self.camera_service.get_all()
        camera_map = {c.camera_id: c for c in all_cameras}

        # Build camera options with value/label for dropdowns
        camera_options = [
            {"value": cid, "label": camera_map[cid].safe_name.replace("_", " ") if cid in camera_map else cid}
            for cid in camera_ids
        ]

        # Get initial preview (all images, no filters)
        preview_data = await self.cleanup_service.get_deletion_preview()

        return {
            "cameras": camera_options,
            "available_dates": dates,
            "available_intervals": intervals,
            # Initial preview data
            "preview": preview_data.get("preview", []),
            "total_count": preview_data.get("total_count", 0),
            "total_size": preview_data.get("total_size", 0),
            "filters": {"camera": None, "date": None, "interval": None},
        }

    async def get_deletion_preview(
        self,
        *,
        camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
    ) -> dict:
        """Get preview of what would be deleted with cascading filter options."""
        # Parse date string if provided
        capture_date = date.fromisoformat(date_str) if date_str else None

        result = await self.cleanup_service.get_deletion_preview(
            camera=camera,
            capture_date=capture_date,
            interval=interval,
        )

        # Get filtered dropdown options based on current selections
        # Cameras: always show all available
        camera_ids = await self.capture_service.get_available_cameras()

        # Get camera objects for display names
        all_cameras = await self.camera_service.get_all()
        camera_map = {c.camera_id: c for c in all_cameras}

        # Build camera options with value/label for dropdowns
        camera_options = [
            {"value": cid, "label": camera_map[cid].safe_name.replace("_", " ") if cid in camera_map else cid}
            for cid in camera_ids
        ]

        # Dates: filter by selected camera
        dates = await self.capture_service.get_available_dates(camera=camera)
        # Intervals: filter by selected camera and date
        intervals = await self.capture_service.get_available_intervals(camera=camera, capture_date=capture_date)

        return {
            **result,
            "cameras": camera_options,
            "available_dates": dates,
            "available_intervals": intervals,
            "filters": {
                "camera": camera,
                "date": capture_date,
                "interval": interval,
            },
        }

    async def delete_images(
        self,
        *,
        camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
    ) -> dict:
        """Delete images matching filters including files, thumbnails, and DB records."""
        # Parse date string if provided
        capture_date = date.fromisoformat(date_str) if date_str else None

        return await self.cleanup_service.delete_by_filters(
            camera=camera,
            capture_date=capture_date,
            interval=interval,
        )
