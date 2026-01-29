"""Camera SQLAlchemy model."""

from datetime import datetime
from typing import TYPE_CHECKING

from db.base import Base, TimestampMixin
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from models.capture import Capture
    from models.timelapse import Timelapse


class Camera(Base, TimestampMixin):
    """Represents a UniFi Protect camera."""

    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    safe_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    mac: Mapped[str | None] = mapped_column(String(17), nullable=True)
    model_key: Mapped[str] = mapped_column(String(32), default="camera", nullable=False)
    video_mode: Mapped[str] = mapped_column(String(32), default="default", nullable=False)
    hdr_type: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)

    is_connected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_recording: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    supports_full_hd_snapshot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_hdr: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_mic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_speaker: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    smart_detect_types: Mapped[list | None] = mapped_column(JSON, nullable=True)

    state: Mapped[str] = mapped_column(String(32), default="DISCONNECTED", nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_capture_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    total_captures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_captures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Per-camera capture settings
    capture_method: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    rtsp_quality: Mapped[str] = mapped_column(String(16), default="high", nullable=False)

    # Intervals enabled for this camera (JSON list, null = use global settings)
    enabled_intervals: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Capability detection results (set during sync/test)
    api_max_resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rtsp_max_resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recommended_method: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Discovery tracking
    first_discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    captures: Mapped[list["Capture"]] = relationship("Capture", back_populates="camera", lazy="dynamic")
    timelapses: Mapped[list["Timelapse"]] = relationship("Timelapse", back_populates="camera", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<Camera(id={self.id}, name={self.name}, safe_name={self.safe_name})>"

    @classmethod
    def from_api_response(cls, camera_data: dict) -> "Camera":
        """Create a Camera instance from UniFi Protect API response."""
        name = camera_data.get("name", "Unknown")
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name.lower())
        feature_flags = camera_data.get("featureFlags", {})

        return cls(
            camera_id=camera_data.get("id", ""),
            name=name,
            safe_name=safe_name,
            mac=camera_data.get("mac"),
            model_key=camera_data.get("modelKey", "camera"),
            video_mode=camera_data.get("videoMode", "default"),
            hdr_type=camera_data.get("hdrType", "auto"),
            is_connected=camera_data.get("state") == "CONNECTED",
            is_recording=camera_data.get("isRecording", False),
            supports_full_hd_snapshot=feature_flags.get("supportFullHdSnapshot", False),
            has_hdr=feature_flags.get("hasHdr", False),
            has_mic=feature_flags.get("hasMic", False),
            has_speaker=feature_flags.get("hasSpeaker", False),
            smart_detect_types=feature_flags.get("smartDetectTypes"),
            state=camera_data.get("state", "DISCONNECTED"),
            last_seen_at=datetime.now() if camera_data.get("state") == "CONNECTED" else None,
        )
