"""Job SQLAlchemy model for tracking timelapse creation jobs."""

from datetime import date, datetime
from uuid import uuid4

from db.base import Base
from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column


def generate_uuid() -> str:
    """Generate a UUID string."""
    return str(uuid4())


class Job(Base):
    """Represents a timelapse creation job."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=generate_uuid, index=True)

    # Job metadata
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    camera_safe_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    camera_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    interval: Mapped[int] = mapped_column(Integer, nullable=False)

    # Job status
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Progress details for UI
    current_frame: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_frames: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_image: Mapped[str | None] = mapped_column(String(512), nullable=True)  # Current image being processed

    # Process tracking
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FFmpeg process ID for killing

    # Override settings
    keep_images: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Timing
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Result
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_file: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_jobs_status_created", "status", "created_at"),
        Index("ix_jobs_status_started", "status", "started_at"),
        Index("ix_jobs_status_completed", "status", "completed_at"),
    )

    def __repr__(self) -> str:
        return f"<Job(job_id={self.job_id}, camera={self.camera_safe_name}, status={self.status})>"

    def to_dict(self) -> dict:
        """Convert job to dictionary for API responses."""
        return {
            "id": self.job_id,
            "title": self.title,
            "camera": self.camera_safe_name,
            "date": self.target_date.isoformat() if self.target_date else None,
            "interval": self.interval,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "keep_images": self.keep_images,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "output_file": self.output_file,
        }


class JobStatus:
    """Job status constants."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
