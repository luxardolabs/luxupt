"""Pydantic schemas for Capture operations."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class CaptureBase(BaseModel):
    """Base schema for Capture data."""

    camera_id: str = Field(..., max_length=64)
    camera_safe_name: str = Field(..., max_length=255)
    timestamp: int
    capture_datetime: datetime
    capture_date: date
    interval: int
    status: str = Field(default="success", max_length=32)
    capture_method: str | None = Field(default=None, max_length=32)


class CaptureCreate(CaptureBase):
    """Schema for creating a new Capture."""

    camera_db_id: int | None = None
    file_path: str | None = Field(default=None, max_length=512)
    file_name: str | None = Field(default=None, max_length=255)
    file_size: int | None = None
    error_message: str | None = None
    capture_duration_ms: int | None = None


class CaptureRead(CaptureBase):
    """Schema for reading Capture data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    camera_db_id: int | None = None
    file_path: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    error_message: str | None = None
    capture_duration_ms: int | None = None
    created_at: datetime
    updated_at: datetime


class CaptureFilters(BaseModel):
    """Schema for capture filtering parameters."""

    camera: str | None = Field(default=None, max_length=255)
    filter_date: date | None = None
    interval: int | None = None
    status: str | None = Field(default=None, max_length=32)
    page: int = 1
    per_page: int = 100


class CaptureStats(BaseModel):
    """Schema for capture statistics."""

    total_captures: int = 0
    successful_captures: int = 0
    failed_captures: int = 0
    unique_cameras: int = 0
    unique_dates: int = 0
    total_file_size: int = 0
    oldest_capture: datetime | None = None
    newest_capture: datetime | None = None


class CaptureSummary(BaseModel):
    """Summary of captures for a specific camera/date combination."""

    camera_safe_name: str = Field(..., max_length=255)
    capture_date: date
    interval: int
    count: int
    success_count: int
    failed_count: int
