"""CRUD operations for database models."""

from crud.activity_crud import activity_crud
from crud.camera_crud import camera_crud
from crud.capture_crud import capture_crud
from crud.fetch_settings_crud import fetch_settings_crud
from crud.job_crud import job_crud
from crud.scheduler_settings_crud import scheduler_settings_crud
from crud.timelapse_crud import timelapse_crud
from crud.user_crud import user_crud

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
