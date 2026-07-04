"""System and settings routes."""

from datetime import datetime
from typing import Annotated

import config
from db.connection import DbSession
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from logging_config import get_logger

from web.auth import get_current_user
from web.deps import SystemViewDep, TemplatesDep, UsersViewDep
from web.main import get_start_time

logger = get_logger(__name__)

router = APIRouter(tags=["system"])


@router.get("", response_class=HTMLResponse)
async def system_page(
    request: Request,
    templates: TemplatesDep,
    view_service: SystemViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the system status page."""
    context = await view_service.get_system_context()

    return templates.TemplateResponse(
        "pages/system.html",
        {"request": request, "user": user, **context},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    templates: TemplatesDep,
    view_service: SystemViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the settings page."""
    context = await view_service.get_settings_context()

    return templates.TemplateResponse(
        "pages/settings.html",
        {"request": request, "user": user, **context},
    )


@router.get("/activity", response_class=HTMLResponse)
async def activity_log_page(
    request: Request,
    templates: TemplatesDep,
    view_service: SystemViewDep,
    activity_type: str | None = Query(None),
    camera_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    user: str = Depends(get_current_user),
) -> Response:
    """Render the activity log page."""
    context = await view_service.get_activity_log_context(
        activity_type=activity_type,
        camera_id=camera_id,
        limit=limit,
    )

    return templates.TemplateResponse(
        "pages/activity.html",
        {"request": request, "user": user, **context},
    )


@router.get("/partials/stats", response_class=HTMLResponse)
async def system_stats_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: SystemViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render system stats partial for HTMX polling."""
    context = await view_service.get_system_context()

    return templates.TemplateResponse(
        "partials/system/stats.html",
        {"request": request, **context},
    )


@router.get("/partials/activity", response_class=HTMLResponse)
async def activity_feed_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: SystemViewDep,
    activity_type: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    user: str = Depends(get_current_user),
) -> Response:
    """Render activity feed partial for HTMX updates."""
    context = await view_service.get_activity_log_context(
        activity_type=activity_type,
        limit=limit,
    )

    return templates.TemplateResponse(
        "partials/activity/activity_feed.html",
        {"request": request, **context},
    )


# =============================================================================
# About page
# =============================================================================


@router.get("/components", response_class=HTMLResponse)
async def components_page(
    request: Request,
    templates: TemplatesDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the component library page (dev mode only)."""
    if config.LOGGING_LEVEL != "DEBUG":
        raise HTTPException(status_code=404, detail="Not found")

    return templates.TemplateResponse(
        "pages/components.html",
        {"request": request, "user": user},
    )


@router.get("/about", response_class=HTMLResponse)
async def about_page(
    request: Request,
    templates: TemplatesDep,
    view_service: SystemViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the about page with all data loaded at once."""
    start_time = get_start_time(request)
    uptime_seconds = (datetime.now() - start_time).total_seconds()

    context = await view_service.get_about_context(uptime_seconds)

    return templates.TemplateResponse(
        "pages/about.html",
        {"request": request, "user": user, **context},
    )


# =============================================================================
# User Management
# =============================================================================


@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    templates: TemplatesDep,
    view_service: UsersViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the user management page."""
    context = await view_service.get_users_context()

    return templates.TemplateResponse(
        "pages/users.html",
        {"request": request, "user": user, **context},
    )


@router.get("/partials/users", response_class=HTMLResponse)
async def users_list_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: UsersViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the users list partial for HTMX updates."""
    context = await view_service.get_users_context()

    return templates.TemplateResponse(
        "partials/system/users_list.html",
        {"request": request, **context},
    )


@router.get("/partials/user-form", response_class=HTMLResponse)
async def user_form_panel(
    request: Request,
    templates: TemplatesDep,
    view_service: UsersViewDep,
    user_id: int | None = Query(None),
    user: str = Depends(get_current_user),
) -> Response:
    """Render the user create/edit form panel."""
    context = await view_service.get_user_form_context(user_id)

    return templates.TemplateResponse(
        "partials/system/user_form_panel.html",
        {"request": request, **context},
    )


@router.post("/users", response_class=HTMLResponse)
async def create_user(
    request: Request,
    templates: TemplatesDep,
    view_service: UsersViewDep,
    db: DbSession,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
    is_admin: Annotated[bool, Form()] = True,
    user: str = Depends(get_current_user),
) -> Response:
    """Create a new user."""
    logger.debug(
        "Create user request",
        extra={"username": username, "is_admin": is_admin, "password_length": len(password)},
    )
    # Validate via view service
    errors = await view_service.validate_user_create(username, password, confirm_password)

    if errors:
        return templates.TemplateResponse(
            "partials/system/user_form_result.html",
            {"request": request, "success": False, "errors": errors},
            status_code=400,
        )

    # Create via view service
    success, message, new_user = await view_service.create_user(username, password, is_admin)
    await db.commit()

    if success and new_user:
        logger.info("User created", extra={"username": new_user.username, "created_by": user})

    return templates.TemplateResponse(
        "partials/system/user_form_result.html",
        {
            "request": request,
            "success": success,
            "message": message if success else None,
            "errors": [message] if not success else None,
        },
        status_code=200 if success else 500,
    )


@router.post("/users/{user_id}", response_class=HTMLResponse)
async def update_user(
    user_id: int,
    request: Request,
    templates: TemplatesDep,
    view_service: UsersViewDep,
    db: DbSession,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()] = "",
    confirm_password: Annotated[str, Form()] = "",
    is_admin: Annotated[bool, Form()] = True,
    user: str = Depends(get_current_user),
) -> Response:
    """Update an existing user."""
    # Validate via view service
    errors = await view_service.validate_user_update(user_id, username, password, confirm_password)

    if errors:
        return templates.TemplateResponse(
            "partials/system/user_form_result.html",
            {"request": request, "success": False, "errors": errors},
            status_code=400,
        )

    # Update via view service
    success, message = await view_service.update_user(user_id, username, password if password else None, is_admin)
    await db.commit()

    if success:
        logger.info("User updated", extra={"user_id": user_id, "username": username, "updated_by": user})

    return templates.TemplateResponse(
        "partials/system/user_form_result.html",
        {
            "request": request,
            "success": success,
            "message": message if success else None,
            "errors": [message] if not success else None,
        },
        status_code=200 if success else 400,
    )


@router.get("/partials/user-delete-confirm/{user_id}", response_class=HTMLResponse)
async def user_delete_confirm_panel(
    user_id: int,
    request: Request,
    templates: TemplatesDep,
    view_service: UsersViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the delete confirmation panel."""
    context = await view_service.get_delete_confirm_context(user_id)

    return templates.TemplateResponse(
        "partials/system/user_delete_confirm.html",
        {"request": request, **context},
    )


@router.delete("/users/{user_id}", response_class=HTMLResponse)
async def delete_user(
    user_id: int,
    request: Request,
    templates: TemplatesDep,
    view_service: UsersViewDep,
    db: DbSession,
    user: str = Depends(get_current_user),
) -> Response:
    """Delete a user."""
    # Delete via view service (handles last-user check)
    success, message = await view_service.delete_user(user_id)
    await db.commit()

    if success:
        logger.info("User deleted", extra={"user_id": user_id, "deleted_by": user})

    return templates.TemplateResponse(
        "partials/system/user_form_result.html",
        {
            "request": request,
            "success": success,
            "message": message if success else None,
            "errors": [message] if not success else None,
        },
        status_code=200 if success else 400,
    )


# =============================================================================
# Backup Settings
# =============================================================================


@router.get("/partials/backup-settings", response_class=HTMLResponse)
async def backup_settings_panel(
    request: Request,
    templates: TemplatesDep,
    view_service: SystemViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the backup settings panel for HTMX."""
    context = await view_service.get_backup_settings_context()

    return templates.TemplateResponse(
        "partials/system/backup_settings_panel.html",
        {"request": request, **context},
    )


@router.post("/backup-settings", response_class=HTMLResponse)
async def update_backup_settings(
    request: Request,
    templates: TemplatesDep,
    view_service: SystemViewDep,
    db: DbSession,
    enabled: Annotated[bool, Form()] = False,
    retention: Annotated[int, Form()] = 5,
    interval_hours: Annotated[int, Form()] = 1,
    backup_dir: Annotated[str, Form()] = "backups",
    user: str = Depends(get_current_user),
) -> Response:
    """Update backup settings."""
    try:
        # Convert interval from hours to seconds
        interval_seconds = interval_hours * 3600

        # If disabled, set retention to 0; otherwise use the submitted value
        effective_retention = retention if enabled else 0

        # Validate retention when enabled
        if enabled and retention < 1:
            return templates.TemplateResponse(
                "partials/system/backup_form_result.html",
                {"request": request, "success": False, "errors": ["Backups to keep must be at least 1"]},
                status_code=400,
            )

        # Validate interval (minimum 1 hour)
        if interval_hours < 1:
            return templates.TemplateResponse(
                "partials/system/backup_form_result.html",
                {"request": request, "success": False, "errors": ["Interval must be at least 1 hour"]},
                status_code=400,
            )

        # Update settings via service
        await view_service.settings_service.update_backup_settings(
            {
                "retention": effective_retention,
                "interval": interval_seconds,
                "backup_dir": backup_dir.strip() or "backups",
            }
        )
        await db.commit()

        logger.info(
            "Backup settings updated",
            extra={
                "enabled": enabled,
                "retention": effective_retention,
                "interval_hours": interval_hours,
                "updated_by": user,
            },
        )

        return templates.TemplateResponse(
            "partials/system/backup_form_result.html",
            {"request": request, "success": True, "message": "Backup settings saved"},
        )

    except Exception as e:
        logger.error("Failed to update backup settings", extra={"error": str(e)})
        return templates.TemplateResponse(
            "partials/system/backup_form_result.html",
            {"request": request, "success": False, "errors": [str(e)]},
            status_code=500,
        )
