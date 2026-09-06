"""Timelapse browser service for timelapse viewing operations."""

from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.crud import timelapse_crud
from app.db.post_commit import after_commit
from app.models.timelapse_model import Timelapse
from app.schemas.timelapse_schema import TimelapseStats
from app.services.core._path_security import validate_video_path
from app.utils import async_fs


class TimelapseBrowserCoreService:
    """Service for browsing and accessing timelapse videos."""

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    async def get_by_id(self, timelapse_id: int) -> Timelapse | None:
        """Get a timelapse by ID."""
        return await timelapse_crud.get(self.db, timelapse_id)

    async def get_by_camera_date_interval(
        self,
        camera_id: str,
        timelapse_date: date,
        interval: int,
    ) -> Timelapse | None:
        """Get a timelapse by camera, date, and interval."""
        return await timelapse_crud.get_by_camera_date_interval(
            self.db,
            camera_id=camera_id,
            timelapse_date=timelapse_date,
            interval=interval,
        )

    async def get_stats(self) -> TimelapseStats:
        """Get timelapse statistics."""
        return await timelapse_crud.get_stats(self.db)

    async def count(self) -> int:
        """Count total timelapses."""
        return await timelapse_crud.count(self.db)

    async def count_by_filters(
        self,
        *,
        camera: str | None = None,
        timelapse_date: date | None = None,
        interval: int | None = None,
        status: str | None = None,
    ) -> int:
        """Count timelapses matching filters."""
        return await timelapse_crud.count_by_filters(
            self.db,
            camera=camera,
            timelapse_date=timelapse_date,
            interval=interval,
            status=status,
        )

    async def get_by_filters(
        self,
        *,
        camera: str | None = None,
        timelapse_date: date | None = None,
        interval: int | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = config.DEFAULT_PAGE_SIZE,
    ) -> list[Timelapse]:
        """Get timelapses matching filters with pagination."""
        return await timelapse_crud.get_by_filters(
            self.db,
            camera=camera,
            timelapse_date=timelapse_date,
            interval=interval,
            status=status,
            skip=skip,
            limit=limit,
        )

    async def get_available_dates(
        self,
        *,
        camera: str | None = None,
        status: str | None = None,
    ) -> list[date]:
        """Get list of dates with timelapses."""
        return await timelapse_crud.get_available_dates(
            self.db,
            camera=camera,
            status=status,
        )

    async def get_available_cameras(
        self,
        *,
        status: str | None = None,
    ) -> list[str]:
        """Get list of cameras with timelapses."""
        return await timelapse_crud.get_available_cameras(self.db, status=status)

    async def get_available_intervals(
        self,
        *,
        camera: str | None = None,
        status: str | None = None,
    ) -> list[int]:
        """Get list of intervals with timelapses."""
        return await timelapse_crud.get_available_intervals(
            self.db,
            camera=camera,
            status=status,
        )

    async def get_video_path(self, timelapse_id: int) -> str | None:
        """Get the video file path for a timelapse with path traversal protection."""
        timelapse = await timelapse_crud.get(self.db, timelapse_id)
        if not timelapse or not timelapse.file_path:
            return None

        # Validate path is within allowed directory
        validated_path = validate_video_path(
            timelapse.file_path,
            context={"timelapse_id": timelapse_id},
        )
        if not validated_path:
            return None

        file_path = Path(validated_path)
        if not await async_fs.path_exists(file_path):
            return None

        return validated_path

    async def get_thumbnail_path(self, timelapse_id: int) -> str | None:
        """Get the thumbnail path for a timelapse with path traversal protection."""
        timelapse = await timelapse_crud.get(self.db, timelapse_id)
        if not timelapse:
            return None

        # Try thumbnail_path first
        if timelapse.thumbnail_path:
            validated_thumb = validate_video_path(
                timelapse.thumbnail_path,
                context={"timelapse_id": timelapse_id, "type": "thumbnail"},
            )
            if validated_thumb:
                thumb_path = Path(validated_thumb)
                if await async_fs.path_exists(thumb_path):
                    return validated_thumb

        # Fallback: generate thumbnail path from video path
        if timelapse.file_path:
            validated_video = validate_video_path(
                timelapse.file_path,
                context={"timelapse_id": timelapse_id},
            )
            if validated_video:
                video_path = Path(validated_video)
                thumb_path = video_path.parent / f"{video_path.stem}_thumb.jpg"
                if await async_fs.path_exists(thumb_path):
                    return str(thumb_path)

        return None

    async def get_video_filename(self, timelapse_id: int) -> str | None:
        """Get the video filename for a timelapse."""
        timelapse = await timelapse_crud.get(self.db, timelapse_id)
        if not timelapse:
            return None

        if timelapse.file_name:
            return timelapse.file_name

        if timelapse.file_path:
            return Path(timelapse.file_path).name

        return None

    async def delete_timelapse(self, timelapse_id: int) -> bool:
        """Delete a timelapse - removes database record and files.

        Returns True if deleted, False if not found.
        """
        timelapse = await timelapse_crud.get(self.db, timelapse_id)
        if not timelapse:
            return False

        # Snapshot paths before the row delete (the ORM object expires after). Remove the
        # DB record FIRST, then unlink the files only after get_db commits (ADR-003 /
        # fw.side_effects_after_commit): a rollback leaves the row intact rather than
        # pointing at deleted files (a leaked file is reclaimable garbage; the reverse is
        # corruption). `missing_ok` makes the post-commit unlink idempotent.
        video_path = timelapse.file_path
        thumbnail_path = timelapse.thumbnail_path
        await timelapse_crud.delete(self.db, id=timelapse_id)
        if video_path is not None:
            vp = Path(video_path)
            after_commit(self.db, lambda: vp.unlink(missing_ok=True))
        if thumbnail_path is not None:
            tp = Path(thumbnail_path)
            after_commit(self.db, lambda: tp.unlink(missing_ok=True))
        return True


async def get_timelapse_browser_service(
    db: AsyncSession,
) -> TimelapseBrowserCoreService:
    """Factory function to create TimelapseBrowserCoreService instance."""
    return TimelapseBrowserCoreService(db)
