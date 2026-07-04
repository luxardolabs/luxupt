"""Dependencies for web routes."""

from typing import Annotated

from db.connection import DbSession
from fastapi import Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates
from services.activity_service import ActivityService
from services.camera_service import CameraService
from services.capture_cleanup_service import CaptureCleanupService
from services.capture_service import CaptureService
from services.capture_stats_service import CaptureStatsService
from services.job_service import JobService
from services.portal import (
    CamerasViewService,
    DashboardViewService,
    ImagesViewService,
    SystemViewService,
    TimelapsesViewService,
    UsersViewService,
)
from services.settings_service import SettingsService
from services.timelapse_browser_service import TimelapseBrowserService
from services.user_service import UserService


# Core service dependencies
async def get_activity_service(db: DbSession) -> ActivityService:
    """Get activity service instance."""
    return ActivityService(db)


async def get_camera_service(db: DbSession) -> CameraService:
    """Get camera service instance."""
    return CameraService(db)


async def get_capture_service(db: DbSession) -> CaptureService:
    """Get capture service instance."""
    return CaptureService(db)


async def get_capture_stats_service(db: DbSession) -> CaptureStatsService:
    """Get capture stats service instance."""
    return CaptureStatsService(db)


async def get_job_service(db: DbSession) -> JobService:
    """Get job service instance."""
    return JobService(db)


async def get_settings_service(db: DbSession) -> SettingsService:
    """Get settings service instance."""
    return SettingsService(db)


async def get_timelapse_browser_service(db: DbSession) -> TimelapseBrowserService:
    """Get timelapse browser service instance."""
    return TimelapseBrowserService(db)


async def get_capture_cleanup_service(db: DbSession) -> CaptureCleanupService:
    """Get capture cleanup service instance."""
    return CaptureCleanupService(db)


async def get_user_service(db: DbSession) -> UserService:
    """Get user service instance."""
    return UserService(db)


# Type aliases for dependency injection - Core services
ActivityServiceDep = Annotated[ActivityService, Depends(get_activity_service)]
CameraServiceDep = Annotated[CameraService, Depends(get_camera_service)]
CaptureCleanupServiceDep = Annotated[CaptureCleanupService, Depends(get_capture_cleanup_service)]
CaptureServiceDep = Annotated[CaptureService, Depends(get_capture_service)]
CaptureStatsServiceDep = Annotated[CaptureStatsService, Depends(get_capture_stats_service)]
JobServiceDep = Annotated[JobService, Depends(get_job_service)]
SettingsServiceDep = Annotated[SettingsService, Depends(get_settings_service)]
TimelapseBrowserServiceDep = Annotated[TimelapseBrowserService, Depends(get_timelapse_browser_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]


# View service dependencies - each receives core services as dependencies
async def get_cameras_view_service(
    db: DbSession,
    camera_service: CameraServiceDep,
    capture_service: CaptureServiceDep,
    capture_stats_service: CaptureStatsServiceDep,
    settings_service: SettingsServiceDep,
) -> CamerasViewService:
    """Get cameras view service instance."""
    return CamerasViewService(
        db,
        camera_service,
        capture_service,
        capture_stats_service,
        settings_service,
    )


async def get_dashboard_view_service(
    camera_service: CameraServiceDep,
    capture_service: CaptureServiceDep,
    capture_stats_service: CaptureStatsServiceDep,
    timelapse_service: TimelapseBrowserServiceDep,
    job_service: JobServiceDep,
    activity_service: ActivityServiceDep,
    settings_service: SettingsServiceDep,
) -> DashboardViewService:
    """Get dashboard view service instance."""
    return DashboardViewService(
        camera_service,
        capture_service,
        capture_stats_service,
        timelapse_service,
        job_service,
        activity_service,
        settings_service,
    )


async def get_images_view_service(
    camera_service: CameraServiceDep,
    capture_service: CaptureServiceDep,
    cleanup_service: CaptureCleanupServiceDep,
) -> ImagesViewService:
    """Get images view service instance."""
    return ImagesViewService(
        camera_service,
        capture_service,
        cleanup_service,
    )


async def get_timelapses_view_service(
    camera_service: CameraServiceDep,
    capture_service: CaptureServiceDep,
    timelapse_service: TimelapseBrowserServiceDep,
    job_service: JobServiceDep,
    settings_service: SettingsServiceDep,
) -> TimelapsesViewService:
    """Get timelapses view service instance."""
    return TimelapsesViewService(
        camera_service,
        capture_service,
        timelapse_service,
        job_service,
        settings_service,
    )


async def get_system_view_service(
    camera_service: CameraServiceDep,
    capture_stats_service: CaptureStatsServiceDep,
    timelapse_service: TimelapseBrowserServiceDep,
    activity_service: ActivityServiceDep,
    settings_service: SettingsServiceDep,
) -> SystemViewService:
    """Get system view service instance."""
    return SystemViewService(
        camera_service,
        capture_stats_service,
        timelapse_service,
        activity_service,
        settings_service,
    )


async def get_users_view_service(
    user_service: UserServiceDep,
) -> UsersViewService:
    """Get users view service instance."""
    return UsersViewService(user_service)


# Type aliases for dependency injection - View services
CamerasViewDep = Annotated[CamerasViewService, Depends(get_cameras_view_service)]
DashboardViewDep = Annotated[DashboardViewService, Depends(get_dashboard_view_service)]
ImagesViewDep = Annotated[ImagesViewService, Depends(get_images_view_service)]
TimelapsesViewDep = Annotated[TimelapsesViewService, Depends(get_timelapses_view_service)]
SystemViewDep = Annotated[SystemViewService, Depends(get_system_view_service)]
UsersViewDep = Annotated[UsersViewService, Depends(get_users_view_service)]


def get_templates(request: Request) -> Jinja2Templates:
    """Get templates from app state."""
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Templates not initialized",
        )
    if not isinstance(templates, Jinja2Templates):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Templates incorrectly initialized",
        )
    return templates


TemplatesDep = Annotated[Jinja2Templates, Depends(get_templates)]
