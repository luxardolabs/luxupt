"""Path security utilities to prevent path traversal attacks."""

from pathlib import Path

import config
from logging_config import get_logger

logger = get_logger(__name__)


def is_safe_image_path(file_path: str | None) -> bool:
    """Check if a path is safely within the image output directory."""
    if not file_path:
        return False
    return _is_safe_path(file_path, config.IMAGE_OUTPUT_PATH)


def is_safe_video_path(file_path: str | None) -> bool:
    """Check if a path is safely within the video output directory."""
    if not file_path:
        return False
    return _is_safe_path(file_path, config.VIDEO_OUTPUT_PATH)


def is_safe_thumbnail_path(file_path: str | None) -> bool:
    """Check if a path is safely within the thumbnail cache directory."""
    if not file_path:
        return False
    return _is_safe_path(file_path, config.THUMBNAIL_CACHE_PATH)


def _is_safe_path(file_path: str, allowed_base: Path) -> bool:
    """Check if a path is safely within the allowed base directory.

    Uses Path.resolve() to resolve symlinks and '..' components,
    then verifies the result is within the allowed directory.
    """
    try:
        resolved = Path(file_path).resolve()
        allowed_resolved = allowed_base.resolve()
        return resolved.is_relative_to(allowed_resolved)
    except (ValueError, OSError):
        return False


def validate_image_path(file_path: str | None, context: dict | None = None) -> str | None:
    """Validate an image path and return it if safe, None otherwise."""
    if not file_path:
        return None

    if not is_safe_image_path(file_path):
        logger.warning(
            "Path traversal attempt blocked for image",
            extra={"path": file_path, **(context or {})},
        )
        return None

    return file_path


def validate_video_path(file_path: str | None, context: dict | None = None) -> str | None:
    """Validate a video path and return it if safe, None otherwise."""
    if not file_path:
        return None

    if not is_safe_video_path(file_path):
        logger.warning(
            "Path traversal attempt blocked for video",
            extra={"path": file_path, **(context or {})},
        )
        return None

    return file_path


def validate_thumbnail_path(file_path: str | None, context: dict | None = None) -> str | None:
    """Validate a thumbnail path and return it if safe, None otherwise."""
    if not file_path:
        return None

    if not is_safe_thumbnail_path(file_path):
        logger.warning(
            "Path traversal attempt blocked for thumbnail",
            extra={"path": file_path, **(context or {})},
        )
        return None

    return file_path
