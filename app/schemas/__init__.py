"""Pydantic schemas for request/response validation."""

from app.schemas.activity_schema import ActivityCreate, ActivityRead, ActivitySummary
from app.schemas.camera_schema import CameraCreate, CameraRead, CameraUpdate
from app.schemas.capture_schema import (
    CaptureCreate,
    CaptureFilters,
    CaptureRead,
    CaptureStats,
)
from app.schemas.fetch_settings_schema import FetchSettingsRead, FetchSettingsUpdate
from app.schemas.job_schema import JobCreate, JobRead, JobUpdate
from app.schemas.scheduler_settings_schema import (
    SchedulerSettingsCreate,
    SchedulerSettingsRead,
    SchedulerSettingsUpdate,
)
from app.schemas.timelapse_schema import (
    TimelapseCreate,
    TimelapseFilters,
    TimelapseRead,
)

__all__ = [
    "ActivityCreate",
    "ActivityRead",
    "ActivitySummary",
    "CameraCreate",
    "CameraRead",
    "CameraUpdate",
    "CaptureCreate",
    "CaptureFilters",
    "CaptureRead",
    "CaptureStats",
    "FetchSettingsRead",
    "FetchSettingsUpdate",
    "JobCreate",
    "JobRead",
    "JobUpdate",
    "SchedulerSettingsCreate",
    "SchedulerSettingsRead",
    "SchedulerSettingsUpdate",
    "TimelapseCreate",
    "TimelapseFilters",
    "TimelapseRead",
]
