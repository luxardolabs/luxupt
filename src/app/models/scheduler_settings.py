"""SchedulerSettings SQLAlchemy model for timelapse scheduler configuration."""

from datetime import datetime, time

from db.base import Base, TimestampMixin
from sqlalchemy import Boolean, DateTime, Integer, String, Time
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column


class SchedulerSettings(Base, TimestampMixin):
    """Singleton model for timelapse scheduler settings."""

    __tablename__ = "scheduler_settings"

    # Always id=1 (singleton pattern)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Master enable/disable toggle (default True = enabled)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Time to run daily
    run_time: Mapped[time] = mapped_column(Time, default=time(1, 0), nullable=False)

    # Days back to process (default 1 = yesterday)
    days_ago: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # List of camera_safe_names to process (null = all cameras)
    enabled_cameras: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # List of intervals to process (default [60] = 60s only)
    enabled_intervals: Mapped[list | None] = mapped_column(JSON, default=[60], nullable=True)

    # Number of concurrent timelapse encodings (default 2)
    concurrent_jobs: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    # Keep source images after successful timelapse creation (default True = keep images)
    keep_images: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Recreate existing timelapses (default True = overwrite if exists)
    recreate_existing: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # When scheduler last ran
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ===========================================
    # FFmpeg/Encoding Settings
    # ===========================================

    # Frame rate for timelapse videos
    frame_rate: Mapped[int] = mapped_column(Integer, default=30, nullable=False)

    # CRF quality factor - lower is better quality
    # Range: 0-51, recommended 18-28
    crf: Mapped[int] = mapped_column(Integer, default=23, nullable=False)

    # Encoding preset - speed vs quality tradeoff
    # Options: ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
    preset: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)

    # Pixel format
    pixel_format: Mapped[str] = mapped_column(String(16), default="yuv420p", nullable=False)

    # FFmpeg timeout in seconds (default: 4 hours)
    ffmpeg_timeout: Mapped[int] = mapped_column(Integer, default=14400, nullable=False)

    def __repr__(self) -> str:
        return f"<SchedulerSettings(enabled={self.enabled}, run_time={self.run_time})>"
