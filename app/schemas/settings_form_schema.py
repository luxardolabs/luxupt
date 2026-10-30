"""What each settings form may submit: the raw form, validated before anything is saved.

Each schema states every bound its form's own inputs show (min/max, the select's options), so a
value the browser would refuse is refused here too: the browser's min/max is only a hint. A
checkbox the browser leaves out is False. An emptied Protect credential clears it; an emptied
number leaves the stored value as it is; every other field has an explicit default. The views build
the save payload from these alone (`to_update()`), never from the raw form.
"""

import re
from datetime import time
from pathlib import PurePosixPath
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enum_model import ScheduleSource

# The ffmpeg tokens the scheduler panel offers (its selects are built from these).
PixelFormat = Literal["yuv420p", "yuv444p", "rgb24"]
Preset = Literal[
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]
PIXEL_FORMATS: tuple[str, ...] = get_args(PixelFormat)
PRESETS: tuple[str, ...] = get_args(Preset)

# A backup folder, relative to the output directory.
_BACKUP_DIR = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*")

CaptureChoice = Literal["auto", "api", "rtsp"]
RtspQuality = Literal["high", "medium", "low"]


def _checkbox(value: object) -> bool:
    """A checkbox is on when the browser sent its value; an unchecked one sends nothing."""
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in {"true", "on", "1"}


def _blank_to_none(value: object) -> object:
    """An empty text input means "no value"."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _intervals(value: list[int] | None) -> list[int] | None:
    """Capture intervals: each 5 to 3600 seconds (the form's bounds), deduplicated and sorted."""
    if not value:
        return None
    for interval in value:
        if not 5 <= interval <= 3600:
            raise ValueError("each interval must be between 5 and 3600 seconds")
    return sorted(set(value))


class _SettingsForm(BaseModel):
    """Shared config: unknown fields are ignored; NaN and infinity are never a number."""

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class FetchSettingsForm(_SettingsForm):
    """The fetch-settings panel (partials/cameras/fetch_settings_panel.html)."""

    enabled: bool = False
    verify_ssl: bool = False
    high_quality_snapshots: bool = False

    intervals: list[int] | None = None
    default_capture_method: CaptureChoice = "auto"
    default_rtsp_quality: RtspQuality = "high"

    # The Protect connection: an emptied field clears the stored value.
    api_key: str | None = Field(default=None, max_length=256)
    base_url: str | None = Field(default=None, max_length=512)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)

    # The numbers: an emptied field (None) leaves the stored value as it is.
    max_retries: int | None = Field(default=None, ge=0, le=10)
    retry_delay: int | None = Field(default=None, ge=1, le=30)
    request_timeout: int | None = Field(default=None, ge=5, le=120)
    rate_limit: int | None = Field(default=None, ge=1, le=100)
    rate_limit_buffer: float | None = Field(default=None, ge=0.1, le=1.0)
    min_offset_seconds: int | None = Field(default=None, ge=1, le=60)
    max_offset_seconds: int | None = Field(default=None, ge=1, le=300)
    camera_refresh_interval: int | None = Field(default=None, ge=60, le=3600)

    rtsp_output_format: Literal["png", "jpg"] = "png"
    png_compression_level: int | None = Field(default=None, ge=0, le=9)
    rtsp_capture_timeout: int | None = Field(default=None, ge=5, le=60)

    _checkboxes = field_validator(
        "enabled", "verify_ssl", "high_quality_snapshots", mode="before"
    )(_checkbox)
    _clearable = field_validator(
        "api_key", "base_url", "username", "password", mode="before"
    )(_blank_to_none)
    _blank_numbers = field_validator(
        "max_retries",
        "retry_delay",
        "request_timeout",
        "rate_limit",
        "rate_limit_buffer",
        "min_offset_seconds",
        "max_offset_seconds",
        "camera_refresh_interval",
        "png_compression_level",
        "rtsp_capture_timeout",
        mode="before",
    )(_blank_to_none)
    _interval_list = field_validator("intervals")(_intervals)

    def to_update(self) -> dict[str, Any]:
        """The fetch_settings columns this form sets: no intervals means 60s; a number only when sent."""
        update = self.model_dump(exclude=_FETCH_NUMBERS)
        update.update(self.model_dump(include=_FETCH_NUMBERS, exclude_none=True))
        update["intervals"] = self.intervals or [60]
        return update


_FETCH_NUMBERS = {
    name
    for name, field in FetchSettingsForm.model_fields.items()
    if field.annotation in (int | None, float | None)
}


class CameraSettingsForm(_SettingsForm):
    """One camera's settings panel (partials/cameras/camera_settings_panel.html)."""

    capture_method: CaptureChoice = "auto"
    rtsp_quality: RtspQuality = "high"
    is_active: bool = False
    # None: the camera captures at every global interval.
    enabled_intervals: list[int] | None = None

    _checkbox_active = field_validator("is_active", mode="before")(_checkbox)
    _interval_list = field_validator("enabled_intervals")(_intervals)

    def to_update(self) -> dict[str, Any]:
        """The camera columns this form sets."""
        return self.model_dump()


class SchedulerSettingsForm(_SettingsForm):
    """The scheduler panel (partials/timelapses/scheduler_panel.html)."""

    enabled: bool = False
    run_time: time
    days_ago: int = Field(ge=1, le=30)
    source: ScheduleSource = ScheduleSource.CAPTURED
    concurrent_jobs: int = Field(ge=1, le=8)
    keep_images: bool = False
    recreate_existing: bool = False
    # None: every camera / every interval.
    enabled_cameras: list[str] | None = None
    enabled_intervals: list[int] | None = None

    # The encoder settings: a form that does not carry them leaves the stored ones as they are.
    frame_rate: int | None = Field(default=None, ge=1, le=120)
    crf: int | None = Field(default=None, ge=0, le=51)
    preset: Preset | None = None
    pixel_format: PixelFormat | None = None
    ffmpeg_timeout: int | None = Field(default=None, ge=300, le=86400)

    _checkboxes = field_validator(
        "enabled", "keep_images", "recreate_existing", mode="before"
    )(_checkbox)
    _blank_encoding = field_validator(
        "frame_rate", "crf", "preset", "pixel_format", "ffmpeg_timeout", mode="before"
    )(_blank_to_none)
    _interval_list = field_validator("enabled_intervals")(_intervals)

    @field_validator("enabled_cameras")
    @classmethod
    def _cameras(cls, value: list[str] | None) -> list[str] | None:
        if not value:
            return None
        for name in value:
            if not name or len(name) > 255:
                raise ValueError("each camera must be a name of 1 to 255 characters")
        return value

    def to_update(self) -> dict[str, Any]:
        """The scheduler columns this form sets; encoder fields only when the form sent them."""
        encoder = {"frame_rate", "crf", "preset", "pixel_format", "ffmpeg_timeout"}
        update = self.model_dump(exclude=encoder)
        update.update(self.model_dump(include=encoder, exclude_none=True))
        return update


class BackupSettingsForm(_SettingsForm):
    """The backup panel (partials/system/backup_settings_panel.html)."""

    enabled: bool = False
    retention: int = Field(ge=1, le=100)
    interval_hours: int = Field(ge=1, le=168)
    backup_dir: str = Field(default="backups", max_length=256)

    _checkbox_enabled = field_validator("enabled", mode="before")(_checkbox)

    @field_validator("backup_dir")
    @classmethod
    def _dir(cls, value: str) -> str:
        """A folder under the output directory: relative, no `..`, letters, digits, `_ - . /`."""
        value = value.strip()
        if not value:
            return "backups"
        value = value.rstrip("/")
        if not _BACKUP_DIR.fullmatch(value) or ".." in PurePosixPath(value).parts:
            raise ValueError("must be a folder name inside the output directory")
        return value

    def to_update(self) -> dict[str, Any]:
        """The backup_settings columns: retention 0 means backups are off."""
        return {
            "retention": self.retention if self.enabled else 0,
            "interval": self.interval_hours * 3600,
            "backup_dir": self.backup_dir,
        }
