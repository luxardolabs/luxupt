"""Image Management Service.

Handles all image-related operations:
- Thumbnail generation (async)
- Image metadata tracking
- Cleanup of old images/thumbnails

Thumbnail storage structure (mirrors image storage):
    {THUMBNAIL_CACHE_PATH}/{camera}/{interval}s/{year}/{month}/{day}/{timestamp}_{size}.jpg
"""

import asyncio
from datetime import date
from pathlib import Path

import config
from logging_config import get_logger
from PIL import Image

logger = get_logger(__name__)


class ImageService:
    """Manages images and thumbnails asynchronously."""

    def __init__(self) -> None:
        self.thumbnail_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._running = False
        self._worker_tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        """Start the image service background workers."""
        if self._running:
            return

        self._running = True
        self._worker_tasks = [
            asyncio.create_task(self._thumbnail_worker(worker_id=i)) for i in range(config.THUMBNAIL_WORKERS)
        ]
        logger.info("Image service started", extra={"workers": config.THUMBNAIL_WORKERS})

    async def stop(self) -> None:
        """Stop the image service."""
        if not self._running:
            return

        self._running = False
        for task in self._worker_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_tasks = []
        logger.info("Image service stopped")

    def build_thumbnail_path(
        self,
        camera_safe_name: str,
        interval: int,
        capture_date: date,
        timestamp: int,
        size: int,
    ) -> Path:
        """Build hierarchical thumbnail path matching image storage structure."""
        year = capture_date.strftime("%Y")
        month = capture_date.strftime("%m")
        day = capture_date.strftime("%d")
        return (
            config.THUMBNAIL_CACHE_PATH
            / camera_safe_name
            / f"{interval}s"
            / year
            / month
            / day
            / f"{timestamp}_{size}.webp"
        )

    async def queue_thumbnail(
        self,
        image_path: str,
        camera_safe_name: str,
        timestamp: int,
        interval: int,
        capture_date: date,
    ) -> None:
        """Queue thumbnail generation requests for both default and large sizes."""
        for size in (config.THUMBNAIL_SIZE_DEFAULT, config.THUMBNAIL_SIZE_LARGE):
            await self.thumbnail_queue.put(
                {
                    "image_path": image_path,
                    "camera_safe_name": camera_safe_name,
                    "timestamp": timestamp,
                    "interval": interval,
                    "capture_date": capture_date,
                    "size": size,
                }
            )

    async def _thumbnail_worker(self, worker_id: int) -> None:
        """Background worker that processes thumbnail generation requests."""
        while self._running:
            try:
                # Wait for a thumbnail request with timeout
                try:
                    request = await asyncio.wait_for(self.thumbnail_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                # Generate thumbnail in thread pool (PIL is blocking)
                capture_date_val = request["capture_date"]
                if isinstance(capture_date_val, date):
                    await asyncio.to_thread(
                        self._generate_thumbnail_sync,
                        str(request["image_path"]),
                        str(request["camera_safe_name"]),
                        int(request["timestamp"]),
                        int(request["interval"]),
                        capture_date_val,
                        int(request["size"]),
                    )

                self.thumbnail_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Thumbnail worker error", extra={"worker_id": worker_id, "error": str(e)})

    def _generate_thumbnail_sync(
        self,
        image_path: str,
        camera_safe_name: str,
        timestamp: int,
        interval: int,
        capture_date: date,
        size: int,
    ) -> str | None:
        """Synchronous thumbnail generation (runs in thread pool)."""
        try:
            thumb_path = self.build_thumbnail_path(camera_safe_name, interval, capture_date, timestamp, size)
            thumb_path.parent.mkdir(parents=True, exist_ok=True)

            with open(image_path, "rb") as f:
                img: Image.Image = Image.open(f)
                # Convert to RGB if necessary (WebP doesn't support all modes)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.thumbnail((size, size), Image.Resampling.LANCZOS)

                with open(thumb_path, "wb") as out_f:
                    img.save(out_f, format="WEBP", quality=82, method=4)

            logger.debug("Generated thumbnail", extra={"thumb_path": str(thumb_path)})
            return str(thumb_path)
        except Exception as e:
            logger.warning("Failed to generate thumbnail", extra={"image_path": image_path, "error": str(e)})
            return None

    def get_thumbnail_path(
        self,
        camera_safe_name: str,
        timestamp: int,
        interval: int,
        capture_date: date,
        size: int | None = None,
    ) -> Path | None:
        """Get the path to a thumbnail if it exists."""
        if size is None:
            size = config.THUMBNAIL_SIZE_DEFAULT
        thumb_path = self.build_thumbnail_path(camera_safe_name, interval, capture_date, timestamp, size)
        return thumb_path if thumb_path.exists() else None


# Global instance
image_service = ImageService()
