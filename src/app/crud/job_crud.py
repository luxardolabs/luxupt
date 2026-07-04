"""CRUD operations for Job model."""

from datetime import date, datetime

import config
from models.job import Job, JobStatus
from schemas.job import JobCreate, JobUpdate
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from crud.base import CRUDBase


class CRUDJob(CRUDBase[Job, JobCreate, JobUpdate]):
    """CRUD operations for Job model."""

    async def get_by_job_id(self, db: AsyncSession, job_id: str) -> Job | None:
        """Get a job by its UUID."""
        result = await db.execute(select(Job).where(Job.job_id == job_id))
        return result.scalar_one_or_none()

    async def get_active(self, db: AsyncSession) -> list[Job]:
        """Get all active (pending or running) jobs."""
        result = await db.execute(
            select(Job)
            .where(or_(Job.status == JobStatus.PENDING, Job.status == JobStatus.RUNNING))
            .order_by(Job.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_pending(self, db: AsyncSession) -> list[Job]:
        """Get all pending jobs."""
        result = await db.execute(select(Job).where(Job.status == JobStatus.PENDING).order_by(Job.created_at.asc()))
        return list(result.scalars().all())

    async def get_running(self, db: AsyncSession) -> list[Job]:
        """Get all running jobs."""
        result = await db.execute(select(Job).where(Job.status == JobStatus.RUNNING).order_by(Job.started_at.asc()))
        return list(result.scalars().all())

    async def get_running_with_pids(self, db: AsyncSession) -> list[Job]:
        """Get all running jobs that have PIDs (for process cleanup)."""
        result = await db.execute(select(Job).where(Job.status == JobStatus.RUNNING, Job.pid.isnot(None)))
        return list(result.scalars().all())

    async def mark_stale_failed(self, db: AsyncSession, error: str) -> int:
        """Mark all running/pending jobs as failed and clear PIDs. Returns count."""
        result = await db.execute(
            update(Job)
            .where(Job.status.in_([JobStatus.RUNNING, JobStatus.PENDING]))
            .values(status=JobStatus.FAILED, error=error, pid=None)
        )
        return result.rowcount  # type: ignore[attr-defined, no-any-return]

    async def get_completed(
        self,
        db: AsyncSession,
        *,
        limit: int = config.DEFAULT_PAGE_SIZE,
    ) -> list[Job]:
        """Get recently completed jobs."""
        result = await db.execute(
            select(Job)
            .where(or_(Job.status == JobStatus.COMPLETED, Job.status == JobStatus.FAILED))
            .order_by(Job.completed_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create_job(
        self,
        db: AsyncSession,
        *,
        title: str,
        camera_safe_name: str,
        target_date: date,
        interval: int,
        camera_id: str | None = None,
        keep_images: bool = True,
    ) -> Job:
        """Create a new timelapse job."""
        job = Job(
            title=title,
            camera_safe_name=camera_safe_name,
            camera_id=camera_id,
            target_date=target_date,
            interval=interval,
            keep_images=keep_images,
            status=JobStatus.PENDING,
            progress=0.0,
            created_at=datetime.now(),
        )
        db.add(job)
        await db.flush()
        await db.refresh(job)
        return job

    async def start_job(self, db: AsyncSession, job_id: str) -> Job | None:
        """Mark a job as started."""
        job = await self.get_by_job_id(db, job_id)
        if not job:
            return None

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now()
        job.message = "Processing..."

        db.add(job)
        await db.flush()
        await db.refresh(job)
        return job

    async def update_progress(
        self,
        db: AsyncSession,
        job_id: str,
        *,
        progress: float,
        message: str | None = None,
        current_frame: int | None = None,
        total_frames: int | None = None,
        current_image: str | None = None,
    ) -> Job | None:
        """Update job progress."""
        job = await self.get_by_job_id(db, job_id)
        if not job:
            return None

        job.progress = min(progress, 100.0)
        if message:
            job.message = message
        if current_frame is not None:
            job.current_frame = current_frame
        if total_frames is not None:
            job.total_frames = total_frames
        if current_image is not None:
            job.current_image = current_image

        db.add(job)
        await db.flush()
        await db.refresh(job)
        return job

    async def complete_job(
        self,
        db: AsyncSession,
        job_id: str,
        *,
        output_file: str | None = None,
        result_details: dict | None = None,
        total_frames: int | None = None,
    ) -> Job | None:
        """Mark a job as completed."""
        job = await self.get_by_job_id(db, job_id)
        if not job:
            return None

        job.status = JobStatus.COMPLETED
        job.progress = 100.0
        job.completed_at = datetime.now()
        job.message = "Completed"
        job.pid = None  # Clear PID on completion
        job.output_file = output_file
        job.result_details = result_details
        if total_frames is not None:
            job.total_frames = total_frames

        db.add(job)
        await db.flush()
        await db.refresh(job)
        return job

    async def fail_job(
        self,
        db: AsyncSession,
        job_id: str,
        *,
        error: str,
    ) -> Job | None:
        """Mark a job as failed."""
        job = await self.get_by_job_id(db, job_id)
        if not job:
            return None

        job.status = JobStatus.FAILED
        job.completed_at = datetime.now()
        job.pid = None  # Clear PID on failure
        # Only set message to "Failed" if no error message was already set
        if not job.message or job.message.startswith("Encoding:") or job.message == "Processing...":
            job.message = error  # Use the error as the message
        job.error = error

        db.add(job)
        await db.flush()
        await db.refresh(job)
        return job

    async def cancel_job(self, db: AsyncSession, job_id: str) -> Job | None:
        """Cancel a pending job."""
        job = await self.get_by_job_id(db, job_id)
        if not job or job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            return None

        job.status = JobStatus.CANCELLED
        job.completed_at = datetime.now()
        job.message = "Cancelled"
        job.pid = None  # Clear PID on cancel

        db.add(job)
        await db.flush()
        await db.refresh(job)
        return job

    async def update_pid(self, db: AsyncSession, job_id: str, pid: int) -> Job | None:
        """Store the FFmpeg process ID for a running job."""
        job = await self.get_by_job_id(db, job_id)
        if not job:
            return None

        job.pid = pid
        db.add(job)
        await db.flush()
        await db.refresh(job)
        return job

    async def clear_pid(self, db: AsyncSession, job_id: str) -> Job | None:
        """Clear the PID when job completes or fails."""
        job = await self.get_by_job_id(db, job_id)
        if not job:
            return None

        job.pid = None
        db.add(job)
        await db.flush()
        await db.refresh(job)
        return job

    async def get_pid(self, db: AsyncSession, job_id: str) -> int | None:
        """Get the PID for a job (for killing the process)."""
        job = await self.get_by_job_id(db, job_id)
        return job.pid if job else None

    async def get_job_for_camera_date(
        self,
        db: AsyncSession,
        *,
        camera: str,
        target_date: date,
        interval: int,
    ) -> Job | None:
        """Check if a job already exists for camera/date/interval."""
        result = await db.execute(
            select(Job).where(
                Job.camera_safe_name == camera,
                Job.target_date == target_date,
                Job.interval == interval,
                or_(Job.status == JobStatus.PENDING, Job.status == JobStatus.RUNNING),
            )
        )
        return result.scalar_one_or_none()

    async def get_summary(self, db: AsyncSession) -> dict:
        """Get job summary statistics."""
        active = await self.get_active(db)
        pending = [j for j in active if j.status == JobStatus.PENDING]
        running = [j for j in active if j.status == JobStatus.RUNNING]

        completed_result = await db.execute(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.COMPLETED)
        )
        completed_count = completed_result.scalar() or 0

        failed_result = await db.execute(select(func.count()).select_from(Job).where(Job.status == JobStatus.FAILED))
        failed_count = failed_result.scalar() or 0

        return {
            "active_jobs": len(active),
            "pending_jobs": len(pending),
            "running_jobs": len(running),
            "completed_jobs": completed_count,
            "failed_jobs": failed_count,
        }


job_crud = CRUDJob(Job)
