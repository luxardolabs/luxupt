"""SQLAlchemy models for the application."""

# Base is re-exported HERE, from the package that imports every model, so `app.models:Base`
# carries COMPLETE metadata. Pointing a schema differ at the bare `app.db.base` instead would
# see only the tables that module's own import chain happened to pull in, and every unimported
# table would read as a false "remove_table" (repo.migrations_build_schema).
from app.db.base import Base
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
    "Base",
    "BackupSettings",
    "Camera",
    "Capture",
    "FetchSettings",
    "Job",
    "SchedulerSettings",
    "Timelapse",
    "User",
]
