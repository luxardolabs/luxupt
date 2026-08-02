"""CRUD operations for database models."""

from app.crud.activity_crud import activity_crud
from app.crud.camera_crud import camera_crud
from app.crud.capture_crud import capture_crud
from app.crud.fetch_settings_crud import fetch_settings_crud
from app.crud.job_crud import job_crud
from app.crud.scheduler_settings_crud import scheduler_settings_crud
from app.crud.timelapse_crud import timelapse_crud
from app.crud.user_crud import user_crud

__all__ = [
    "activity_crud",
    "camera_crud",
    "capture_crud",
    "fetch_settings_crud",
    "job_crud",
    "scheduler_settings_crud",
    "timelapse_crud",
    "user_crud",
]
