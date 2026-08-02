"""SQLAlchemy models for the application."""

from app.models.activity_model import Activity
from app.models.backup_settings_model import BackupSettings
from app.models.camera_model import Camera
from app.models.capture_model import Capture
from app.models.fetch_settings_model import FetchSettings
from app.models.job_model import Job
from app.models.scheduler_settings_model import SchedulerSettings
from app.models.timelapse_model import Timelapse
from app.models.user_model import User

__all__ = [
    "Activity",
    "BackupSettings",
    "Camera",
    "Capture",
    "FetchSettings",
    "Job",
    "SchedulerSettings",
    "Timelapse",
    "User",
]
