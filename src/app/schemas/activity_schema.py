"""Pydantic schemas for Activity operations."""

from datetime import datetime

import config
from pydantic import BaseModel, ConfigDict, Field


class ActivityBase(BaseModel):
    """Base schema for Activity data."""

    activity_type: str = Field(..., max_length=64)
    message: str
    camera_id: str | None = Field(default=None, max_length=64)
    camera_safe_name: str | None = Field(default=None, max_length=255)
    interval: int | None = None
    details: dict | None = None


class ActivityCreate(ActivityBase):
    """Schema for creating a new Activity."""

    timestamp: datetime


class ActivityRead(ActivityBase):
    """Schema for reading Activity data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime


class ActivityFilters(BaseModel):
    """Schema for activity filtering parameters."""

    activity_type: str | None = Field(default=None, max_length=64)
    camera_id: str | None = Field(default=None, max_length=64)
    since: datetime | None = None
    limit: int = config.DEFAULT_PAGE_SIZE


class ActivitySummary(BaseModel):
    """Schema for activity summary statistics."""

    total_events: int = 0
    capture_success_count: int = 0
    capture_failed_count: int = 0
    timelapse_started_count: int = 0
    timelapse_completed_count: int = 0
    timelapse_failed_count: int = 0
    error_count: int = 0
    cameras_online: int = 0
    cameras_offline: int = 0
