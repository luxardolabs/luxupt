"""Pydantic schemas for request/response validation."""

from schemas.activity import ActivityCreate, ActivityRead, ActivitySummary
from schemas.camera import CameraCreate, CameraRead, CameraUpdate
from schemas.capture import CaptureCreate, CaptureFilters, CaptureRead, CaptureStats
from schemas.fetch_settings import FetchSettingsRead, FetchSettingsUpdate
from schemas.job import JobCreate, JobRead, JobUpdate
from schemas.scheduler_settings import (
    SchedulerSettingsCreate,
    SchedulerSettingsRead,
    SchedulerSettingsUpdate,
)
from schemas.timelapse import TimelapseCreate, TimelapseFilters, TimelapseRead

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
