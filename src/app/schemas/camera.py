"""Pydantic schemas for Camera operations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CameraBase(BaseModel):
    """Base schema for Camera data."""

    camera_id: str = Field(..., max_length=64)
    name: str = Field(..., max_length=255)
    safe_name: str = Field(..., max_length=255)
    mac: str | None = Field(default=None, max_length=17)
    model_key: str = Field(default="camera", max_length=32)
    video_mode: str = Field(default="default", max_length=32)
    hdr_type: str = Field(default="auto", max_length=16)
    is_connected: bool = False
    is_recording: bool = False
    is_active: bool = True
    supports_full_hd_snapshot: bool = False
    has_hdr: bool = False
    has_mic: bool = False
    has_speaker: bool = False
    smart_detect_types: list[str] | None = None
    state: str = Field(default="DISCONNECTED", max_length=32)

    # Per-camera capture settings
    capture_method: str = Field(default="auto", max_length=16)
    rtsp_quality: str = Field(default="high", max_length=16)
    enabled_intervals: list[int] | None = None

    # Capability detection results
    api_max_resolution: str | None = Field(default=None, max_length=32)
    rtsp_max_resolution: str | None = Field(default=None, max_length=32)
    recommended_method: str | None = Field(default=None, max_length=16)


class CameraCreate(CameraBase):
    """Schema for creating a new Camera."""

    pass


class CameraUpdate(BaseModel):
    """Schema for updating a Camera."""

    name: str | None = Field(default=None, max_length=255)
    is_connected: bool | None = None
    is_recording: bool | None = None
    is_active: bool | None = None
    state: str | None = Field(default=None, max_length=32)
    last_seen_at: datetime | None = None
    last_capture_at: datetime | None = None
    total_captures: int | None = None
    failed_captures: int | None = None

    # Per-camera capture settings (user-editable)
    capture_method: str | None = Field(default=None, max_length=16)
    rtsp_quality: str | None = Field(default=None, max_length=16)
    enabled_intervals: list[int] | None = None

    # Capability detection (updated by system)
    api_max_resolution: str | None = Field(default=None, max_length=32)
    rtsp_max_resolution: str | None = Field(default=None, max_length=32)
    recommended_method: str | None = Field(default=None, max_length=16)


class CameraRead(CameraBase):
    """Schema for reading Camera data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    last_seen_at: datetime | None = None
    last_capture_at: datetime | None = None
    total_captures: int = 0
    failed_captures: int = 0
    first_discovered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CameraWithStats(CameraRead):
    """Camera with additional statistics."""

    success_rate: float = 0.0
    latest_capture_path: str | None = None
    latest_capture_timestamp: int | None = None
