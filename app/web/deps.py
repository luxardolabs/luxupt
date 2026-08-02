"""Dependencies for web routes."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from app.db.connection import DbSession
from app.services.core.activity_core_service import ActivityCoreService
from app.services.core.camera_core_service import CameraCoreService
from app.services.core.capture_cleanup_core_service import CaptureCleanupCoreService
from app.services.core.capture_core_service import CaptureCoreService
from app.services.core.capture_stats_core_service import CaptureStatsCoreService
from app.services.core.job_core_service import JobCoreService
from app.services.core.settings_core_service import SettingsCoreService
from app.services.core.timelapse_browser_core_service import TimelapseBrowserCoreService
from app.services.core.user_core_service import UserCoreService
from app.services.views import (
    CamerasViewService,
    DashboardViewService,
    ImagesViewService,
    SystemViewService,
    TimelapsesViewService,
    UsersViewService,
)


# Core service dependencies
async def get_activity_service(db: DbSession) -> ActivityCoreService:
    """Get activity service instance."""
    return ActivityCoreService(db)


async def get_camera_service(db: DbSession) -> CameraCoreService:
    """Get camera service instance."""
    return CameraCoreService(db)


async def get_capture_service(db: DbSession) -> CaptureCoreService:
    """Get capture service instance."""
    return CaptureCoreService(db)


async def get_capture_stats_service(db: DbSession) -> CaptureStatsCoreService:
    """Get capture stats service instance."""
    return CaptureStatsCoreService(db)


async def get_job_service(db: DbSession) -> JobCoreService:
    """Get job service instance."""
    return JobCoreService(db)


async def get_settings_service(db: DbSession) -> SettingsCoreService:
    """Get settings service instance."""
    return SettingsCoreService(db)


async def get_timelapse_browser_service(db: DbSession) -> TimelapseBrowserCoreService:
    """Get timelapse browser service instance."""
    return TimelapseBrowserCoreService(db)


async def get_capture_cleanup_service(db: DbSession) -> CaptureCleanupCoreService:
    """Get capture cleanup service instance."""
    return CaptureCleanupCoreService(db)


async def get_user_service(db: DbSession) -> UserCoreService:
    """Get user service instance."""
    return UserCoreService(db)


# Type aliases for dependency injection - Core services
ActivityServiceDep = Annotated[ActivityCoreService, Depends(get_activity_service)]
CameraServiceDep = Annotated[CameraCoreService, Depends(get_camera_service)]
CaptureCleanupServiceDep = Annotated[
    CaptureCleanupCoreService, Depends(get_capture_cleanup_service)
]
CaptureServiceDep = Annotated[CaptureCoreService, Depends(get_capture_service)]
CaptureStatsServiceDep = Annotated[
    CaptureStatsCoreService, Depends(get_capture_stats_service)
]
JobServiceDep = Annotated[JobCoreService, Depends(get_job_service)]
SettingsServiceDep = Annotated[SettingsCoreService, Depends(get_settings_service)]
TimelapseBrowserServiceDep = Annotated[
    TimelapseBrowserCoreService, Depends(get_timelapse_browser_service)
]
UserServiceDep = Annotated[UserCoreService, Depends(get_user_service)]


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
    db: DbSession,
    camera_service: CameraServiceDep,
    capture_service: CaptureServiceDep,
    timelapse_service: TimelapseBrowserServiceDep,
    job_service: JobServiceDep,
    settings_service: SettingsServiceDep,
) -> TimelapsesViewService:
    """Get timelapses view service instance."""
    return TimelapsesViewService(
        db,
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
TimelapsesViewDep = Annotated[
    TimelapsesViewService, Depends(get_timelapses_view_service)
]
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
