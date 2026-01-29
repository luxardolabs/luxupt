"""FetchSettings SQLAlchemy model for global fetch/capture configuration."""

from db.base import Base, TimestampMixin
from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column


class FetchSettings(Base, TimestampMixin):
    """Singleton model for global fetch/capture settings."""

    __tablename__ = "fetch_settings"

    # Always id=1 (singleton pattern)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Master enable/disable toggle
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Available intervals (JSON list of seconds, e.g., [15, 60, 180])
    # These are the intervals the system will capture at
    intervals: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Default capture method for new cameras ("auto", "api", "rtsp")
    default_capture_method: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)

    # Default RTSP quality for new cameras ("high", "medium", "low")
    default_rtsp_quality: Mapped[str] = mapped_column(String(16), default="high", nullable=False)

    # ===========================================
    # API Connection Settings (override env vars)
    # ===========================================

    # UniFi Protect API key (null = use env var)
    api_key: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # UniFi Protect base URL (null = use env var)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # SSL verification (default False for self-signed certs common with UniFi Protect)
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ===========================================
    # Fetch Reliability Settings
    # ===========================================

    # Maximum retries for failed fetches
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    # Delay between retries in seconds
    retry_delay: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    # Request timeout in seconds
    request_timeout: Mapped[int] = mapped_column(Integer, default=30, nullable=False)

    # ===========================================
    # Quality Settings
    # ===========================================

    # Request high quality snapshots 1080p+
    high_quality_snapshots: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # RTSP output format: "png" (higher quality, larger) or "jpg" (smaller, faster)
    rtsp_output_format: Mapped[str] = mapped_column(String(8), default="png", nullable=False)

    # PNG compression level 0-9 (0=none/fast, 9=max/slow, 6=good balance)
    png_compression_level: Mapped[int] = mapped_column(Integer, default=6, nullable=False)

    # RTSP capture timeout in seconds (per camera)
    rtsp_capture_timeout: Mapped[int] = mapped_column(Integer, default=15, nullable=False)

    # ===========================================
    # Rate Limiting Settings
    # ===========================================

    # UniFi Protect rate limit requests/sec
    rate_limit: Mapped[int] = mapped_column(Integer, default=10, nullable=False)

    # Safety buffer percentage 0.0-1.0
    rate_limit_buffer: Mapped[float] = mapped_column(Float, default=0.8, nullable=False)

    # ===========================================
    # Camera Distribution Settings
    # ===========================================

    # Minimum offset between camera captures in seconds
    min_offset_seconds: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    # Maximum offset between camera captures in seconds
    max_offset_seconds: Mapped[int] = mapped_column(Integer, default=15, nullable=False)

    # ===========================================
    # Camera Refresh Settings
    # ===========================================

    # How often to check for new cameras in seconds
    camera_refresh_interval: Mapped[int] = mapped_column(Integer, default=300, nullable=False)

    def __repr__(self) -> str:
        return f"<FetchSettings(enabled={self.enabled}, intervals={self.intervals})>"

    def get_intervals(self) -> list[int]:
        """Get intervals list, with fallback to default."""
        if self.intervals:
            return self.intervals
        # Default intervals if none configured
        return [60]
