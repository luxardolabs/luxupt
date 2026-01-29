"""Pydantic schemas for Job operations."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class JobBase(BaseModel):
    """Base schema for Job data."""

    title: str = Field(..., max_length=255)
    camera_safe_name: str = Field(..., max_length=255)
    target_date: date
    interval: int
    override_deletion: bool = False


class JobCreate(JobBase):
    """Schema for creating a new Job."""

    camera_id: str | None = Field(default=None, max_length=64)


class JobUpdate(BaseModel):
    """Schema for updating a Job."""

    status: str | None = Field(default=None, max_length=32)
    progress: float | None = None
    message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    output_file: str | None = Field(default=None, max_length=512)
    result_details: dict | None = None


class JobRead(JobBase):
    """Schema for reading Job data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: str = Field(..., max_length=36)
    camera_id: str | None = Field(default=None, max_length=64)
    status: str = Field(..., max_length=32)
    progress: float
    message: str | None = None
    current_frame: int = 0
    total_frames: int = 0
    current_image: str | None = Field(default=None, max_length=512)
    pid: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    output_file: str | None = Field(default=None, max_length=512)
    result_details: dict | None = None


class JobSummary(BaseModel):
    """Schema for job summary."""

    active_jobs: int = 0
    pending_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
