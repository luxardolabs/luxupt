# app/config.py
"""
Minimal configuration - startup-time settings only.

Most settings are now stored in the database (FetchSettings, SchedulerSettings).
This file only contains settings that MUST be known at startup before the database
is available, or settings that cannot change at runtime (like file paths).
"""

import json
import os
from pathlib import Path

from logging_config import get_logger

logger = get_logger(__name__)

# =============================================================================
# UNIFI PROTECT API (env var fallback when not set in database)
# =============================================================================

UNIFI_PROTECT_BASE_URL = os.getenv("UNIFI_PROTECT_BASE_URL", "")
UNIFI_PROTECT_API_KEY = os.getenv("UNIFI_PROTECT_API_KEY", "")
UNIFI_PROTECT_VERIFY_SSL = os.getenv("UNIFI_PROTECT_VERIFY_SSL", "false").lower() in ["true", "1", "yes"]

# =============================================================================
# PATH CONFIGURATIONS (cannot change at runtime)
# =============================================================================
# All paths default to subdirectories under output/. For tiered storage
# (e.g., NVMe scratch + HDD archive), mount separate host paths to each
# subdirectory in Docker — no env var changes needed. See docs/configuration.md.

# Captured snapshots (write-heavy, ephemeral — deleted after timelapse compilation)
IMAGE_OUTPUT_PATH = Path(os.getenv("IMAGE_OUTPUT_PATH", "output/images"))

# Compiled timelapse videos (write-once, archival — kept long-term)
VIDEO_OUTPUT_PATH = Path(os.getenv("VIDEO_OUTPUT_PATH", "output/videos"))

# Cached thumbnails for web UI (ephemeral, regenerated as needed)
THUMBNAIL_CACHE_PATH = Path(os.getenv("THUMBNAIL_CACHE_PATH", "output/thumbnails"))

# =============================================================================
# THUMBNAIL SETTINGS (startup-time, affects worker count)
# =============================================================================

THUMBNAIL_SIZE_DEFAULT = int(os.getenv("THUMBNAIL_SIZE_DEFAULT", "256"))
THUMBNAIL_SIZE_LARGE = int(os.getenv("THUMBNAIL_SIZE_LARGE", "512"))
THUMBNAIL_WORKERS = int(os.getenv("THUMBNAIL_WORKERS", "4"))

# =============================================================================
# DATABASE SETTINGS (connection-time)
# =============================================================================

DATABASE_TIMEOUT = int(os.getenv("DATABASE_TIMEOUT", "30"))
DATABASE_BUSY_TIMEOUT = int(os.getenv("DATABASE_BUSY_TIMEOUT", "30000"))
DATABASE_MMAP_SIZE = int(os.getenv("DATABASE_MMAP_SIZE", str(64 * 1024 * 1024)))  # 64MB
DATABASE_CACHE_SIZE_KB = int(os.getenv("DATABASE_CACHE_SIZE_KB", "20000"))  # 20MB
DATABASE_POOL_SIZE = int(os.getenv("DATABASE_POOL_SIZE", "20"))
DATABASE_POOL_MAX_OVERFLOW = int(os.getenv("DATABASE_POOL_MAX_OVERFLOW", "40"))

# =============================================================================
# LOGGING CONFIGURATION (startup-time)
# =============================================================================

LOGGING_LEVEL = os.getenv("LOGGING_LEVEL", "INFO").upper()
LOGGING_FORMAT = os.getenv("LOGGING_FORMAT", "json").lower()

# Per-module log level overrides (JSON dict)
_module_levels_str = os.getenv("LOGGING_MODULE_LEVELS", "{}")
try:
    LOGGING_MODULE_LEVELS: dict[str, str] = json.loads(_module_levels_str)
except (json.JSONDecodeError, TypeError):
    LOGGING_MODULE_LEVELS = {}

# =============================================================================
# WEB INTERFACE CONFIGURATION (startup-time)
# =============================================================================

WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
WEB_DEV_RELOAD = os.getenv("WEB_DEV_RELOAD", "False").lower() in ["true", "1", "yes"]
WEB_SESSION_SECRET = os.getenv("WEB_SESSION_SECRET", "")  # Auto-generated if empty
WEB_CORS_ORIGINS = os.getenv("WEB_CORS_ORIGINS", "").split(",") if os.getenv("WEB_CORS_ORIGINS") else []

# Cookie security mode: "auto", "always", "never"
WEB_COOKIE_SECURE_MODE = os.getenv("WEB_COOKIE_SECURE_MODE", "auto").lower()
WEB_TRUST_PROXY_HEADERS = os.getenv("WEB_TRUST_PROXY_HEADERS", "True").lower() in ["true", "1", "yes"]

# Environment-based authentication (backward compatibility)
# If both are set, env auth takes priority over database users
WEB_USERNAME = os.getenv("WEB_USERNAME", "")
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "")

# Login rate limiting
WEB_LOGIN_RATE_LIMIT = int(os.getenv("WEB_LOGIN_RATE_LIMIT", "5"))
WEB_LOGIN_RATE_WINDOW_SECONDS = int(os.getenv("WEB_LOGIN_RATE_WINDOW_SECONDS", "60"))

# JWT token expiry
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))  # 7 days

# =============================================================================
# PAGINATION DEFAULTS
# =============================================================================

DEFAULT_PAGE_SIZE = int(os.getenv("DEFAULT_PAGE_SIZE", "100"))
MAX_PAGE_SIZE = int(os.getenv("MAX_PAGE_SIZE", "1000"))
RECENT_ITEMS_LIMIT = int(os.getenv("RECENT_ITEMS_LIMIT", "10"))

# =============================================================================
# SETTINGS RELOAD (how often to check DB for setting changes)
# =============================================================================

SETTINGS_RELOAD_INTERVAL = int(os.getenv("SETTINGS_RELOAD_INTERVAL", "15"))

# =============================================================================
# PROGRESS UPDATE INTERVALS
# =============================================================================

# How often progress updates (UI + DB) fire during timelapse encoding (seconds)
PROGRESS_UPDATE_INTERVAL = float(os.getenv("PROGRESS_UPDATE_INTERVAL", "15.0"))

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def ensure_directories() -> None:
    """Create necessary output directories."""
    IMAGE_OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    VIDEO_OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    THUMBNAIL_CACHE_PATH.mkdir(parents=True, exist_ok=True)
