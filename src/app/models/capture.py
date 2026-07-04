"""Capture SQLAlchemy model."""

from datetime import date, datetime
from typing import TYPE_CHECKING

from db.base import Base, TimestampMixin
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from models.camera import Camera


class Capture(Base, TimestampMixin):
    """Represents a captured snapshot from a camera."""

    __tablename__ = "captures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Camera reference (denormalized for query performance)
    camera_db_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    camera_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    camera_safe_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Capture metadata
    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    capture_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capture_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    interval: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # File information
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Status and error tracking
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    capture_method: Mapped[str | None] = mapped_column(String(32), nullable=True)  # api, rtsp, auto
    capture_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationship
    camera: Mapped["Camera"] = relationship("Camera", back_populates="captures")

    __table_args__ = (
        # Primary query indexes using camera_id
        Index("ix_captures_camera_id_date", "camera_id", "capture_date"),
        Index("ix_captures_camera_id_interval", "camera_id", "interval"),
        Index("ix_captures_camera_id_timestamp", "camera_id", "timestamp"),
        Index("ix_captures_date_interval", "capture_date", "interval"),
        # Index for image browser: filter by status, order by timestamp DESC, id DESC
        Index("ix_captures_status_timestamp_id", "status", "timestamp", "id"),
        # Legacy indexes using camera_safe_name (for display/file path lookups)
        Index("ix_captures_camera_date", "camera_safe_name", "capture_date"),
        Index("ix_captures_camera_interval", "camera_safe_name", "interval"),
        Index("ix_captures_camera_timestamp", "camera_safe_name", "timestamp"),
    )

    def __repr__(self) -> str:
        return f"<Capture(id={self.id}, camera={self.camera_safe_name}, timestamp={self.timestamp})>"
