"""Timelapse SQLAlchemy model."""

from datetime import date, datetime
from typing import TYPE_CHECKING

from db.base import Base, TimestampMixin
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from models.camera import Camera


class Timelapse(Base, TimestampMixin):
    """Represents a generated timelapse video."""

    __tablename__ = "timelapses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Camera reference
    camera_db_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    camera_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    camera_safe_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Timelapse metadata
    timelapse_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    interval: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    frame_count: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    # File information
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)  # e.g., "1920x1080"
    thumbnail_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Status tracking
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Processing metadata
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationship
    camera: Mapped["Camera"] = relationship("Camera", back_populates="timelapses")

    __table_args__ = (
        # Primary query indexes using camera_id
        Index("ix_timelapses_camera_id_date", "camera_id", "timelapse_date"),
        Index("ix_timelapses_camera_id_interval", "camera_id", "interval"),
        Index("ix_timelapses_date_interval", "timelapse_date", "interval"),
        # Legacy indexes using camera_safe_name (for display/file path lookups)
        Index("ix_timelapses_camera_date", "camera_safe_name", "timelapse_date"),
        Index("ix_timelapses_camera_interval", "camera_safe_name", "interval"),
    )

    def __repr__(self) -> str:
        return f"<Timelapse(id={self.id}, camera={self.camera_safe_name}, date={self.timelapse_date})>"
