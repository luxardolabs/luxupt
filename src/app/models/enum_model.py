"""Canonical enum module — closed-set values are enum-typed at every layer (LUXARCH ADR-001).

StrEnum members compare and render as their value, and str_enum() keeps the
column VARCHAR (values stored, not member names) — no migration needed.
"""

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def str_enum(enum_cls: type[StrEnum], length: int = 32) -> SAEnum:
    """VARCHAR-backed SQLAlchemy Enum type storing member *values* (not names)."""
    return SAEnum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        length=length,
    )


class CaptureStatus(StrEnum):
    """Outcome of a single snapshot capture."""

    SUCCESS = "success"
    FAILED = "failed"


class CaptureMethod(StrEnum):
    """How a snapshot is (or should be) captured."""

    AUTO = "auto"  # resolve per-camera at fetch time
    API = "api"  # Protect snapshot API
    RTSP = "rtsp"  # RTSP stream frame grab
    PROTECT_HISTORICAL = "protect_historical"  # recording-snapshot backfill


class TimelapseStatus(StrEnum):
    """Lifecycle of a rendered timelapse video."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(StrEnum):
    """Kind of timelapse job."""

    LIVE_DAILY = "live_daily"
    HISTORICAL = "historical"
    HISTORICAL_COMBINED = "historical_combined"


class JobStatus(StrEnum):
    """Lifecycle of a timelapse job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActivityType(StrEnum):
    """Category of an activity-log entry."""

    CAPTURE_SUCCESS = "capture_success"
    CAPTURE_FAILED = "capture_failed"
    CAMERA_ONLINE = "camera_online"
    CAMERA_OFFLINE = "camera_offline"
    TIMELAPSE_STARTED = "timelapse_started"
    TIMELAPSE_COMPLETED = "timelapse_completed"
    TIMELAPSE_FAILED = "timelapse_failed"
    SERVICE_STARTED = "service_started"
    SERVICE_STOPPED = "service_stopped"
    WEB_REQUEST = "web_request"
    # Referenced by the stats aggregate and fetch-cycle-skip logging; was missing
    # from the old constants class (latent AttributeError surfaced by enum typing).
    ERROR = "error"
