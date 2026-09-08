"""Images view service for preparing image browser template data."""

import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.schemas.pagination_schema import build_pagination
from app.services.core.camera_core_service import CameraCoreService
from app.services.core.capture_cleanup_core_service import CaptureCleanupCoreService
from app.services.core.capture_core_service import CaptureCoreService
from app.services.core.image_core_service import image_service
from app.services.views._camera_options import (
    CaptureIdentity,
    build_camera_options,
    build_date_options,
)
from app.utils import async_fs


class ImagesViewService:
    """Prepares data for image browsing pages."""

    def __init__(
        self,
        camera_service: CameraCoreService,
        capture_service: CaptureCoreService,
        cleanup_service: CaptureCleanupCoreService,
    ):
        """Initialize with core services."""
        self.camera_service = camera_service
        self.capture_service = capture_service
        self.cleanup_service = cleanup_service

    async def serve_thumbnail(
        self, camera: str, interval: int, capture_date: date, timestamp: int, size: int
    ) -> FileResponse:
        """Resolve a capture thumbnail and build the response for it.

        The view owns the RESPONSE (fw.no_passthrough_view_service): it resolves the path
        via core, tolerates the generation queue still working, and shapes the FileResponse
        with its cache headers. The router just returns what this hands back.
        """
        thumb_path = image_service.build_thumbnail_path(
            camera, interval, capture_date, timestamp, size
        )

        if not await async_fs.path_exists(thumb_path):
            # Thumbnail may still be in the generation queue — wait briefly
            for _ in range(40):
                await asyncio.sleep(0.05)
                if await async_fs.path_exists(thumb_path):
                    break
            else:
                raise HTTPException(status_code=404, detail="Thumbnail not found")

        return FileResponse(
            thumb_path,
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=86400"},
        )

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

        return await self.capture_service.get_validated_file_path(
            camera.camera_id, timestamp, interval
        )

    async def get_capture_for_thumbnail(
        self,
        camera_safe_name: str,
        interval: int,
        timestamp: int,
    ) -> dict[str, Any] | None:
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
        capture = await self.capture_service.get_by_camera_and_timestamp(
            camera_id, timestamp, interval=interval
        )
        if not capture:
            return None

        # Validate file path
        file_path, exists = await self.capture_service.get_validated_file_path(
            camera_id, timestamp
        )
        if not file_path:
            return None

        return {
            "file_path": file_path,
            "camera_safe_name": capture.camera_safe_name,
            "interval": capture.interval,
            "capture_date": capture.capture_date,
        }

    @staticmethod
    def _build_image_card_urls(
        images: Sequence[CaptureIdentity],
        *,
        camera: str | None,
        date_str: str | None,
        interval: int | None,
    ) -> dict[int, str]:
        """Lightbox URL per capture, keyed by capture id (fw.url_assembly_in_view).

        The card macro used to assemble this itself: three conditional appends, a join, and a
        concat — real logic in the render layer. Each URL carries the active filters so
        lightbox navigation stays inside the current result set, plus the capture's OWN
        interval, because the same timestamp can exist at several intervals. urlencode
        escapes the values; the string concatenation did not.
        """
        urls: dict[int, str] = {}
        for image in images:
            query = urlencode(
                {
                    key: value
                    for key, value in (
                        ("camera", camera),
                        ("date", date_str),
                        ("interval", image.interval),
                    )
                    if value
                }
            )
            urls[image.id] = (
                f"/images/lightbox/{image.camera_safe_name}/{image.timestamp}?{query}"
            )
        return urls

    async def get_browser_context(
        self,
        *,
        camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> dict[str, Any]:
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
        available_intervals = await self.capture_service.get_available_intervals(
            camera=camera
        )

        # Get stats for total storage size
        capture_stats = await self.capture_service.get_capture_stats()
        total_size_gb = round(capture_stats.total_file_size / 1024 / 1024 / 1024, 2)

        return {
            "images": images,
            "image_urls": self._build_image_card_urls(
                images, camera=camera, date_str=date_str, interval=interval
            ),
            "cameras": cameras,
            "camera_options": build_camera_options(cameras),
            "available_dates": available_dates,
            "available_date_options": build_date_options(available_dates),
            "available_intervals": available_intervals,
            "total_size_gb": total_size_gb,
            "filters": {
                "camera": camera,
                "date": capture_date,
                "interval": interval,
            },
            "pagination": build_pagination(page=page, per_page=per_page, total=total),
        }

    async def get_image_grid_context(
        self,
        *,
        camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> dict[str, Any]:
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

        return {
            "images": images,
            "image_urls": self._build_image_card_urls(
                images, camera=camera, date_str=date_str, interval=interval
            ),
            "filters": {
                "camera": camera,
                "date": capture_date,
                "interval": interval,
            },
            "pagination": build_pagination(page=page, per_page=per_page, total=total),
        }

    async def get_lightbox_context(
        self,
        camera_safe_name: str,
        timestamp: int,
        *,
        filter_camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
    ) -> dict[str, Any]:
        """Get lightbox context with current image and prev/next for navigation."""
        # Parse date string if provided
        capture_date = date.fromisoformat(date_str) if date_str else None

        # Lookup camera to get camera_id for queries
        camera = await self.camera_service.get_by_safe_name(camera_safe_name)
        if not camera:
            return {
                "image": None,
                "prev_image": None,
                "next_image": None,
                "prev_url": None,
                "next_url": None,
            }

        camera_id = camera.camera_id

        # Get the current image (include interval to handle same-timestamp different-interval captures)
        image = await self.capture_service.get_by_camera_and_timestamp(
            camera_id,
            timestamp,
            interval=interval,
        )

        if not image:
            return {
                "image": None,
                "prev_image": None,
                "next_image": None,
                "prev_url": None,
                "next_url": None,
            }

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

        # The view assembles the navigation URLs; the template renders them
        # (fw.no_template_logic). Each URL carries the image's OWN interval so navigation is
        # unambiguous, plus whatever filters are active. urlencode escapes the values, which
        # the previous string concatenation in the template did not.
        def _nav_url(safe_name: str, timestamp: int, target_interval: int) -> str:
            query = urlencode(
                {
                    key: value
                    for key, value in (
                        ("camera", filter_camera),
                        ("date", date_str),
                        ("interval", target_interval),
                    )
                    if value
                }
            )
            return f"/images/lightbox/{safe_name}/{timestamp}/content?{query}"

        return {
            "image": image,
            "prev_image": prev_image,
            "next_image": next_image,
            "prev_url": (
                _nav_url(
                    prev_image.camera_safe_name,
                    prev_image.timestamp,
                    prev_image.interval,
                )
                if prev_image
                else None
            ),
            "next_url": (
                _nav_url(
                    next_image.camera_safe_name,
                    next_image.timestamp,
                    next_image.interval,
                )
                if next_image
                else None
            ),
        }

    async def get_delete_panel_context(self) -> dict[str, Any]:
        """Get context for the image deletion panel including initial preview."""
        camera_ids = await self.capture_service.get_available_cameras()
        dates = await self.capture_service.get_available_dates()
        intervals = await self.capture_service.get_available_intervals()

        # Get camera objects for display names
        all_cameras = await self.camera_service.get_all()
        camera_map = {c.camera_id: c for c in all_cameras}

        # Build camera options with value/label for dropdowns
        camera_options = [
            {
                "value": cid,
                "label": camera_map[cid].safe_name.replace("_", " ")
                if cid in camera_map
                else cid,
            }
            for cid in camera_ids
        ]

        # Get initial preview (all images, no filters)
        preview_data = await self.cleanup_service.get_deletion_preview()

        return {
            "cameras": camera_options,
            "available_dates": dates,
            "available_date_options": build_date_options(dates),
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
    ) -> dict[str, Any]:
        """Get preview of what would be deleted with cascading filter options.

        Args:
            camera: camera_id (UUID) from dropdown.
        """
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
            {
                "value": cid,
                "label": camera_map[cid].safe_name.replace("_", " ")
                if cid in camera_map
                else cid,
            }
            for cid in camera_ids
        ]

        # Dates: filter by selected camera
        dates = await self.capture_service.get_available_dates(camera=camera)
        # Intervals: filter by selected camera and date
        intervals = await self.capture_service.get_available_intervals(
            camera=camera, capture_date=capture_date
        )

        return {
            **result,
            "cameras": camera_options,
            "available_dates": dates,
            "available_date_options": build_date_options(dates),
            "available_intervals": intervals,
            "filters": {
                "camera": camera,
                "date": capture_date,
                "interval": interval,
            },
            # The template was hand-assembling this query string with chained `~` and
            # inline conditionals — real logic in the render layer (fw.no_template_logic).
            # urlencode also escapes the values, which the concatenation did not.
            "delete_url": "/images/delete?"
            + urlencode(
                {
                    key: value
                    for key, value in (
                        ("camera", camera),
                        ("date", capture_date),
                        ("interval", interval),
                    )
                    if value
                }
            ),
        }

    async def delete_images(
        self,
        *,
        camera: str | None = None,
        date_str: str | None = None,
        interval: int | None = None,
    ) -> dict[str, Any]:
        """Delete images matching filters including files, thumbnails, and DB records."""
        # Parse date string if provided
        capture_date = date.fromisoformat(date_str) if date_str else None

        return await self.cleanup_service.delete_by_filters(
            camera=camera,
            capture_date=capture_date,
            interval=interval,
        )
