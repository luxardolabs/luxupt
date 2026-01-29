"""Pydantic schemas for FetchSettings operations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FetchSettingsBase(BaseModel):
    """Base schema for FetchSettings."""

    enabled: bool = True
    intervals: list[int] | None = None
    default_capture_method: str = Field(default="auto", max_length=16)
    default_rtsp_quality: str = Field(default="high", max_length=16)

    # API Connection (null = use env var)
    base_url: str | None = Field(default=None, max_length=512)
    verify_ssl: bool | None = None

    # Fetch reliability
    max_retries: int = 3
    retry_delay: int = 2
    request_timeout: int = 30

    # Quality settings
    high_quality_snapshots: bool = True
    rtsp_output_format: str = Field(default="png", max_length=8)
    png_compression_level: int = Field(default=6, ge=0, le=9)
    rtsp_capture_timeout: int = 15

    # Rate limiting
    rate_limit: int = 10
    rate_limit_buffer: float = 0.8

    # Camera distribution
    min_offset_seconds: int = 2
    max_offset_seconds: int = 15

    # Camera refresh
    camera_refresh_interval: int = 300


class FetchSettingsUpdate(BaseModel):
    """Schema for updating FetchSettings - all fields optional."""

    enabled: bool | None = None
    intervals: list[int] | None = None
    default_capture_method: str | None = Field(default=None, max_length=16)
    default_rtsp_quality: str | None = Field(default=None, max_length=16)

    # API Connection
    base_url: str | None = Field(default=None, max_length=512)
    verify_ssl: bool | None = None

    # Fetch reliability
    max_retries: int | None = None
    retry_delay: int | None = None
    request_timeout: int | None = None

    # Quality settings
    high_quality_snapshots: bool | None = None
    rtsp_output_format: str | None = Field(default=None, max_length=8)
    png_compression_level: int | None = Field(default=None, ge=0, le=9)
    rtsp_capture_timeout: int | None = None

    # Rate limiting
    rate_limit: int | None = None
    rate_limit_buffer: float | None = None

    # Camera distribution
    min_offset_seconds: int | None = None
    max_offset_seconds: int | None = None

    # Camera refresh
    camera_refresh_interval: int | None = None


class FetchSettingsRead(FetchSettingsBase):
    """Schema for reading FetchSettings."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
