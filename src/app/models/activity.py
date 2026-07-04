"""Activity SQLAlchemy model."""

from datetime import datetime

from db.base import Base
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column


class Activity(Base):
    """Represents an activity event in the system."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Event metadata
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    activity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional context
    camera_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    camera_safe_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    interval: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Additional details stored as JSON
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_activities_type_timestamp", "activity_type", "timestamp"),
        Index("ix_activities_camera_timestamp", "camera_id", "timestamp"),
    )

    def __repr__(self) -> str:
        return f"<Activity(id={self.id}, type={self.activity_type}, timestamp={self.timestamp})>"

    def to_dict(self) -> dict:
        """Convert activity to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "activity_type": self.activity_type,
            "message": self.message,
            "camera_id": self.camera_id,
            "camera_safe_name": self.camera_safe_name,
            "interval": self.interval,
            "details": self.details,
        }


class ActivityType:
    """Activity type constants."""

    CAPTURE_SUCCESS = "capture_success"
    CAPTURE_FAILED = "capture_failed"
    CAMERA_ONLINE = "camera_online"
    CAMERA_OFFLINE = "camera_offline"
    TIMELAPSE_STARTED = "timelapse_started"
    TIMELAPSE_COMPLETED = "timelapse_completed"
    TIMELAPSE_FAILED = "timelapse_failed"
    SERVICE_STARTED = "service_started"
    SERVICE_STOPPED = "service_stopped"
    WEB_REQUEST = "web_request"
    ERROR = "error"
    INFO = "info"
