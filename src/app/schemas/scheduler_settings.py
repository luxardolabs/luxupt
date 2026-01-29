"""Pydantic schemas for SchedulerSettings operations."""

from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field


class SchedulerSettingsBase(BaseModel):
    """Base schema for SchedulerSettings data."""

    enabled: bool = False
    run_time: time = time(1, 0)
    days_ago: int = 1
    enabled_cameras: list[str] | None = None
    enabled_intervals: list[int] | None = None

    # Concurrent job settings
    concurrent_jobs: int = 2
    keep_images: bool = True
    recreate_existing: bool = True

    # FFmpeg/Encoding settings
    frame_rate: int = 30
    crf: int = 23
    preset: str = Field(default="medium", max_length=16)
    pixel_format: str = Field(default="yuv420p", max_length=16)
    ffmpeg_timeout: int = 14400


class SchedulerSettingsCreate(SchedulerSettingsBase):
    """Schema for creating SchedulerSettings."""

    pass


class SchedulerSettingsUpdate(BaseModel):
    """Schema for updating SchedulerSettings - all fields optional for PATCH."""

    enabled: bool | None = None
    run_time: time | None = None
    days_ago: int | None = None
    enabled_cameras: list[str] | None = None
    enabled_intervals: list[int] | None = None

    # Concurrent job settings
    concurrent_jobs: int | None = None
    keep_images: bool | None = None
    recreate_existing: bool | None = None

    # FFmpeg/Encoding settings
    frame_rate: int | None = None
    crf: int | None = None
    preset: str | None = Field(default=None, max_length=16)
    pixel_format: str | None = Field(default=None, max_length=16)
    ffmpeg_timeout: int | None = None


class SchedulerSettingsRead(SchedulerSettingsBase):
    """Schema for reading SchedulerSettings data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
