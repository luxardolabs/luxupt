"""Camera routes for camera management and HTMX partials."""

from typing import TYPE_CHECKING, Annotated

import config
from db.connection import DbSession, async_session
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from logging_config import get_logger

from web.auth import get_current_user
from web.deps import CamerasViewDep, DashboardViewDep, TemplatesDep

if TYPE_CHECKING:
    from camera_manager import CameraManagerSettings

logger = get_logger(__name__)


async def _load_camera_manager_settings() -> "CameraManagerSettings":
    """Load settings for CameraManager from database."""
    from camera_manager import CameraManagerSettings
    from crud.fetch_settings_crud import fetch_settings_crud

    async with async_session() as session:
        fetch_settings = await fetch_settings_crud.get_settings(session)

        return CameraManagerSettings(
            base_url=config.UNIFI_PROTECT_BASE_URL or fetch_settings.base_url or "",
            api_key=config.UNIFI_PROTECT_API_KEY or fetch_settings.api_key or "",
            verify_ssl=config.UNIFI_PROTECT_VERIFY_SSL if config.UNIFI_PROTECT_BASE_URL else fetch_settings.verify_ssl,
            request_timeout=fetch_settings.request_timeout,
            rate_limit=fetch_settings.rate_limit,
            rate_limit_buffer=fetch_settings.rate_limit_buffer,
            min_offset_seconds=fetch_settings.min_offset_seconds,
            max_offset_seconds=fetch_settings.max_offset_seconds,
            camera_refresh_interval=fetch_settings.camera_refresh_interval,
        )


router = APIRouter(tags=["cameras"])


@router.get("", response_class=HTMLResponse)
async def cameras_page(
    request: Request,
    templates: TemplatesDep,
    view_service: DashboardViewDep,
    cameras_view_service: CamerasViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the cameras overview page."""
    context = await view_service.get_camera_cards_context()

    # Check if API setup is needed (no env vars and no database settings)
    fetch_context = await cameras_view_service.get_fetch_settings_context()
    api_config = fetch_context.get("api_config", {})
    needs_api_setup = not api_config.get("has_api_key") and not api_config.get("has_base_url")

    return templates.TemplateResponse(
        "pages/cameras.html",
        {"request": request, "user": user, "needs_api_setup": needs_api_setup, **context},
    )


@router.get("/partials/list", response_class=HTMLResponse)
async def camera_list_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: DashboardViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render camera list partial for HTMX updates."""
    context = await view_service.get_camera_cards_context()
    return templates.TemplateResponse(
        "partials/cameras/camera_list.html",
        {"request": request, **context},
    )


@router.get("/partials/card/{camera_safe_name}", response_class=HTMLResponse)
async def camera_card_partial(
    camera_safe_name: str,
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render single camera card partial."""
    context = await view_service.get_camera_card_context(camera_safe_name)

    return templates.TemplateResponse(
        "partials/cameras/camera_card.html",
        {"request": request, **context},
    )


@router.get("/fetch-settings", response_class=HTMLResponse)
async def fetch_settings_panel(
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render global fetch settings panel."""
    context = await view_service.get_fetch_settings_context()

    return templates.TemplateResponse(
        "partials/cameras/fetch_settings_panel.html",
        {"request": request, **context},
    )


@router.post("/fetch-settings", response_class=HTMLResponse)
async def save_fetch_settings(
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    db: DbSession,
    enabled: Annotated[str | None, Form()] = None,
    intervals: Annotated[list[int] | None, Form()] = None,
    default_capture_method: Annotated[str, Form()] = "auto",
    default_rtsp_quality: Annotated[str, Form()] = "high",
    # API connection settings
    api_key: Annotated[str | None, Form()] = None,
    base_url: Annotated[str | None, Form()] = None,
    verify_ssl: Annotated[str | None, Form()] = None,
    # Reliability settings
    max_retries: Annotated[int | None, Form()] = None,
    retry_delay: Annotated[int | None, Form()] = None,
    request_timeout: Annotated[int | None, Form()] = None,
    # Rate limiting
    rate_limit: Annotated[int | None, Form()] = None,
    rate_limit_buffer: Annotated[float | None, Form()] = None,
    # Camera distribution
    min_offset_seconds: Annotated[int | None, Form()] = None,
    max_offset_seconds: Annotated[int | None, Form()] = None,
    # Other settings
    camera_refresh_interval: Annotated[int | None, Form()] = None,
    high_quality_snapshots: Annotated[str | None, Form()] = None,
    # RTSP settings
    rtsp_output_format: Annotated[str | None, Form()] = None,
    png_compression_level: Annotated[int | None, Form()] = None,
    rtsp_capture_timeout: Annotated[int | None, Form()] = None,
    # Disabled cameras to re-enable
    reactivate_cameras: Annotated[list[str] | None, Form()] = None,
    user: str = Depends(get_current_user),
) -> Response:
    """Save global fetch settings."""
    try:
        # Build update data
        update_data = {
            "enabled": enabled == "true",
            "default_capture_method": default_capture_method,
            "default_rtsp_quality": default_rtsp_quality,
        }

        # Handle intervals - filter out invalid values and sort
        if intervals:
            valid_intervals = sorted([i for i in intervals if i >= 5])
            update_data["intervals"] = valid_intervals if valid_intervals else [60]
        else:
            update_data["intervals"] = [60]

        # API connection settings (empty string means clear)
        update_data["api_key"] = api_key if api_key else None
        update_data["base_url"] = base_url if base_url else None
        # Checkbox: "true" = checked (verify), absent/None = unchecked (don't verify)
        update_data["verify_ssl"] = verify_ssl == "true"

        # Reliability settings (None means use env var defaults)
        update_data["max_retries"] = max_retries if max_retries else None
        update_data["retry_delay"] = retry_delay if retry_delay else None
        update_data["request_timeout"] = request_timeout if request_timeout else None

        # Rate limiting
        update_data["rate_limit"] = rate_limit if rate_limit else None
        update_data["rate_limit_buffer"] = rate_limit_buffer if rate_limit_buffer else None

        # Camera distribution
        update_data["min_offset_seconds"] = min_offset_seconds if min_offset_seconds else None
        update_data["max_offset_seconds"] = max_offset_seconds if max_offset_seconds else None

        # Other settings
        update_data["camera_refresh_interval"] = camera_refresh_interval if camera_refresh_interval else None
        update_data["high_quality_snapshots"] = high_quality_snapshots == "true" if high_quality_snapshots else None

        # RTSP settings
        update_data["rtsp_output_format"] = rtsp_output_format if rtsp_output_format else None
        update_data["png_compression_level"] = png_compression_level if png_compression_level is not None else None
        update_data["rtsp_capture_timeout"] = rtsp_capture_timeout if rtsp_capture_timeout else None

        success, message, cameras_synced = await view_service.update_fetch_settings(update_data)

        # Re-enable any toggled-on disabled cameras
        if reactivate_cameras:
            for cam_id in reactivate_cameras:
                await view_service.update_camera_settings(cam_id, {"is_active": True})

        await db.commit()

        logger.info("Updated fetch settings", extra={"update_data": update_data, "cameras_synced": cameras_synced})

        if cameras_synced is not None and cameras_synced < 0:
            # Connection failed
            return templates.TemplateResponse(
                "partials/cameras/camera_settings_result.html",
                {
                    "request": request,
                    "success": False,
                    "error": "Settings saved, but connection test failed",
                    "details": message,
                },
            )

        response = templates.TemplateResponse(
            "partials/cameras/camera_settings_result.html",
            {
                "request": request,
                "success": success,
                "message": message,
            },
        )
        if reactivate_cameras:
            response.headers["HX-Trigger"] = "camera-list-refresh"
        return response

    except Exception as e:
        logger.error("Error saving fetch settings", extra={"error": str(e)})
        return templates.TemplateResponse(
            "partials/cameras/camera_settings_result.html",
            {
                "request": request,
                "success": False,
                "error": "Failed to save fetch settings. Check server logs for details.",
            },
        )


@router.get("/capture-stats", response_class=HTMLResponse)
async def capture_stats_panel(
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render capture statistics panel."""
    context = await view_service.get_capture_stats_context()

    return templates.TemplateResponse(
        "partials/cameras/capture_stats_panel.html",
        {"request": request, **context},
    )


@router.get("/capture-stats/charts", response_class=HTMLResponse)
async def capture_stats_charts(
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    camera: str | None = None,
    interval: int | None = None,
    period: str = "24h",
    offset: int = 0,
    user: str = Depends(get_current_user),
) -> Response:
    """Render capture statistics charts partial."""
    context = await view_service.get_capture_stats_charts_context(
        camera=camera,
        interval=interval,
        period=period,
        offset=offset,
    )

    return templates.TemplateResponse(
        "partials/cameras/capture_stats_charts.html",
        {"request": request, **context},
    )


@router.get("/{camera_id}/settings", response_class=HTMLResponse)
async def camera_settings_panel(
    camera_id: str,
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render camera settings panel (slide-out)."""
    context = await view_service.get_camera_settings_context(camera_id)
    if not context["camera"]:
        return HTMLResponse("<div>Camera not found</div>", status_code=404)

    return templates.TemplateResponse(
        "partials/cameras/camera_settings_panel.html",
        {"request": request, **context},
    )


@router.post("/{camera_id}/settings", response_class=HTMLResponse)
async def save_camera_settings(
    camera_id: str,
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    db: DbSession,
    capture_method: Annotated[str, Form()] = "auto",
    rtsp_quality: Annotated[str, Form()] = "high",
    enabled_intervals: Annotated[list[int] | None, Form()] = None,
    is_active: Annotated[str | None, Form()] = None,
    user: str = Depends(get_current_user),
) -> Response:
    """Save camera capture settings."""
    try:
        # Build update data
        update_data = {
            "capture_method": capture_method,
            "rtsp_quality": rtsp_quality,
            "is_active": is_active == "true",
        }

        # Handle enabled_intervals - empty list means use all (null)
        if enabled_intervals:
            update_data["enabled_intervals"] = enabled_intervals
        else:
            update_data["enabled_intervals"] = None

        success, message = await view_service.update_camera_settings(camera_id, update_data)
        await db.commit()

        if not success:
            return templates.TemplateResponse(
                "partials/cameras/camera_settings_result.html",
                {"request": request, "success": False, "error": message},
            )

        logger.info("Updated camera settings", extra={"camera_id": camera_id, "update_data": update_data})

        return templates.TemplateResponse(
            "partials/cameras/camera_settings_result.html",
            {
                "request": request,
                "success": True,
                "message": message,
            },
        )

    except Exception as e:
        logger.error("Error saving camera settings", extra={"error": str(e)})
        return templates.TemplateResponse(
            "partials/cameras/camera_settings_result.html",
            {
                "request": request,
                "success": False,
                "error": "Failed to save camera settings. Check server logs for details.",
            },
        )


@router.post("/{camera_id}/detect", response_class=HTMLResponse)
async def detect_camera_capabilities(
    camera_id: str,
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    db: DbSession,
    user: str = Depends(get_current_user),
) -> Response:
    """Run capability detection for a camera."""
    from camera_manager import CameraManager

    try:
        cm_settings = await _load_camera_manager_settings()
        async with CameraManager(cm_settings) as manager:
            capabilities = await view_service.detect_camera_capabilities(camera_id, manager)

        if capabilities is None:
            return templates.TemplateResponse(
                "partials/cameras/camera_settings_result.html",
                {"request": request, "success": False, "error": "Camera not found or not connected"},
            )

        await db.commit()

        logger.info("Capability detection complete", extra={"camera_id": camera_id, "capabilities": capabilities})

        return templates.TemplateResponse(
            "partials/cameras/camera_settings_result.html",
            {
                "request": request,
                "detected": True,
                "api_resolution": capabilities.get("api_max_resolution"),
                "rtsp_resolution": capabilities.get("rtsp_max_resolution"),
                "recommended": capabilities.get("recommended_method"),
            },
        )

    except Exception as e:
        logger.error("Error detecting camera capabilities", extra={"error": str(e)})
        return templates.TemplateResponse(
            "partials/cameras/camera_settings_result.html",
            {
                "request": request,
                "success": False,
                "error": "Failed to detect camera capabilities. Check server logs for details.",
            },
        )


@router.delete("/{camera_id}", response_class=HTMLResponse)
async def delete_camera(
    camera_id: str,
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    db: DbSession,
    user: str = Depends(get_current_user),
) -> Response:
    """Delete a camera from the database.

    Files on disk are NOT deleted. Related captures/timelapses will have their
    camera reference set to NULL.
    """
    success, message = await view_service.delete_camera(camera_id)
    await db.commit()

    return templates.TemplateResponse(
        "partials/cameras/camera_settings_result.html",
        {
            "request": request,
            "success": success,
            "message": message if success else None,
            "error": message if not success else None,
        },
    )


@router.get("/{camera_safe_name}/panel", response_class=HTMLResponse)
async def camera_panel(
    camera_safe_name: str,
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render camera detail panel (slide-out)."""
    context = await view_service.get_camera_panel_context(camera_safe_name)
    if not context["camera"]:
        return HTMLResponse("<div>Camera not found</div>", status_code=404)

    return templates.TemplateResponse(
        "partials/cameras/camera_panel.html",
        {"request": request, **context},
    )


@router.get("/{camera_safe_name}", response_class=HTMLResponse)
async def camera_detail_page(
    camera_safe_name: str,
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render camera detail page."""
    context = await view_service.get_camera_detail_context(camera_safe_name)
    if not context["camera"]:
        return templates.TemplateResponse(
            "pages/404.html",
            {"request": request, "message": "Camera not found"},
            status_code=404,
        )

    return templates.TemplateResponse(
        "pages/camera_detail.html",
        {"request": request, "user": user, **context},
    )
