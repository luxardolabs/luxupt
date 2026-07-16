"""Pydantic schemas for Timelapse operations."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class TimelapseBase(BaseModel):
    """Base schema for Timelapse data."""

    camera_id: str = Field(..., max_length=64)
    camera_safe_name: str = Field(..., max_length=255)
    timelapse_date: date
    interval: int
    frame_count: int
    frame_rate: int
    duration_seconds: float


class TimelapseCreate(TimelapseBase):
    """Schema for creating a new Timelapse."""

    camera_db_id: int | None = None
    file_path: str | None = Field(default=None, max_length=512)
    file_name: str | None = Field(default=None, max_length=255)
    file_size: int | None = None
    resolution: str | None = Field(default=None, max_length=32)
    status: str = Field(default="pending", max_length=32)


class TimelapseUpdate(BaseModel):
    """Schema for updating a Timelapse."""

    status: str | None = Field(default=None, max_length=32)
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    processing_time_seconds: float | None = None
    file_path: str | None = Field(default=None, max_length=512)
    file_name: str | None = Field(default=None, max_length=255)
    file_size: int | None = None
    resolution: str | None = Field(default=None, max_length=32)


class TimelapseRead(TimelapseBase):
    """Schema for reading Timelapse data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    camera_db_id: int | None = None
    file_path: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    resolution: str | None = None
    status: str
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    processing_time_seconds: float | None = None
    created_at: datetime
    updated_at: datetime


class TimelapseFilters(BaseModel):
    """Schema for timelapse filtering parameters."""

    camera: str | None = Field(default=None, max_length=255)
    filter_date: date | None = None
    interval: int | None = None
    status: str | None = Field(default=None, max_length=32)
    page: int = 1
    per_page: int = 50


class TimelapseStats(BaseModel):
    """Schema for timelapse statistics."""

    total_timelapses: int = 0
    completed_timelapses: int = 0
    pending_timelapses: int = 0
    failed_timelapses: int = 0
    total_duration_seconds: float = 0.0
    total_file_size: int = 0
