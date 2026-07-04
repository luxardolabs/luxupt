# app/startup.py

"""Startup utilities for banner and configuration display."""

import os
import platform
from datetime import datetime

import config
from logging_config import get_logger

logger = get_logger(__name__)


def print_banner() -> None:
    """Print application banner with version information."""
    banner = """
    ██╗     ██╗   ██╗██╗  ██╗██╗   ██╗██████╗ ████████╗
    ██║     ██║   ██║╚██╗██╔╝██║   ██║██╔══██╗╚══██╔══╝
    ██║     ██║   ██║ ╚███╔╝ ██║   ██║██████╔╝   ██║
    ██║     ██║   ██║ ██╔██╗ ██║   ██║██╔═══╝    ██║
    ███████╗╚██████╔╝██╔╝ ██╗╚██████╔╝██║        ██║
    ╚══════╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚═╝        ╚═╝
    """

    # Get version info from environment variables set during Docker build
    version = os.environ.get("LUXUPT_VERSION", "dev")
    build_date = os.environ.get("LUXUPT_BUILD_DATE", "unknown")

    # Use logging instead of print
    for line in banner.split("\n"):
        if line.strip():  # Only log non-empty lines
            logger.info(line)

    # Version and build information
    logger.info(
        "Application info",
        extra={
            "version": version,
            "build_date": build_date,
            "author": "Dave Schmid (lux4rd0)",
            "repository": "https://github.com/luxardolabs/luxupt",
        },
    )

    # System information
    logger.info(
        "System info",
        extra={
            "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
            "python": platform.python_version(),
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


def print_configuration() -> None:
    """Print startup-time configuration (env vars only). Database settings logged after init."""
    logger.info(
        "Startup configuration",
        extra={
            "web_port": config.WEB_PORT,
            "image_path": str(config.IMAGE_OUTPUT_PATH),
            "video_path": str(config.VIDEO_OUTPUT_PATH),
            "thumbnail_path": str(config.THUMBNAIL_CACHE_PATH),
            "log_level": config.LOGGING_LEVEL,
            "log_format": config.LOGGING_FORMAT,
        },
    )
