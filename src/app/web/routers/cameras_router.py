"""Camera routes for camera management and HTMX partials."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from logging_config import get_logger

from web.auth import get_current_user
from web.deps import CamerasViewDep, DashboardViewDep, TemplatesDep

logger = get_logger(__name__)

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

    return templates.TemplateResponse(
        "pages/cameras.html",
        {"request": request, "user": user, "needs_api_setup": fetch_context["needs_api"], **context},
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
    enabled: Annotated[str | None, Form()] = None,
    intervals: Annotated[list[int] | None, Form()] = None,
    default_capture_method: Annotated[str, Form()] = "auto",
    default_rtsp_quality: Annotated[str, Form()] = "high",
    # API connection settings
    api_key: Annotated[str | None, Form()] = None,
    base_url: Annotated[str | None, Form()] = None,
    username: Annotated[str | None, Form()] = None,
    password: Annotated[str | None, Form()] = None,
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
        success, message, cameras_synced, reactivated = await view_service.save_fetch_settings(
            enabled=enabled,
            intervals=intervals,
            default_capture_method=default_capture_method,
            default_rtsp_quality=default_rtsp_quality,
            api_key=api_key,
            base_url=base_url,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            max_retries=max_retries,
            retry_delay=retry_delay,
            request_timeout=request_timeout,
            rate_limit=rate_limit,
            rate_limit_buffer=rate_limit_buffer,
            min_offset_seconds=min_offset_seconds,
            max_offset_seconds=max_offset_seconds,
            camera_refresh_interval=camera_refresh_interval,
            high_quality_snapshots=high_quality_snapshots,
            rtsp_output_format=rtsp_output_format,
            png_compression_level=png_compression_level,
            rtsp_capture_timeout=rtsp_capture_timeout,
            reactivate_cameras=reactivate_cameras,
        )

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
        if reactivated:
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


@router.post("/fetch-settings/test-protect-connection", response_class=HTMLResponse)
async def test_protect_connection(
    request: Request,
    templates: TemplatesDep,
    view_service: CamerasViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Test the configured Protect username/password by logging in and pulling one snapshot.

    Uses whatever creds are currently in the DB or env vars. Returns a small
    HTML partial for HTMX to swap inline next to the Test Connection button.
    """
    context = await view_service.test_protect_connection()

    return templates.TemplateResponse(
        "partials/cameras/protect_test_result.html",
        {"request": request, **context},
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
        return templates.TemplateResponse(
            "partials/cameras/camera_not_found.html",
            {"request": request},
            status_code=404,
        )

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
    capture_method: Annotated[str, Form()] = "auto",
    rtsp_quality: Annotated[str, Form()] = "high",
    enabled_intervals: Annotated[list[int] | None, Form()] = None,
    is_active: Annotated[str | None, Form()] = None,
    user: str = Depends(get_current_user),
) -> Response:
    """Save camera capture settings."""
    try:
        success, message = await view_service.save_camera_settings(
            camera_id,
            capture_method=capture_method,
            rtsp_quality=rtsp_quality,
            enabled_intervals=enabled_intervals,
            is_active=is_active,
        )

        if not success:
            return templates.TemplateResponse(
                "partials/cameras/camera_settings_result.html",
                {"request": request, "success": False, "error": message},
            )

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
    user: str = Depends(get_current_user),
) -> Response:
    """Run capability detection for a camera."""
    try:
        capabilities = await view_service.run_capability_detection(camera_id)

        if capabilities is None:
            return templates.TemplateResponse(
                "partials/cameras/camera_settings_result.html",
                {"request": request, "success": False, "error": "Camera not found or not connected"},
            )

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
    user: str = Depends(get_current_user),
) -> Response:
    """Delete a camera from the database.

    Files on disk are NOT deleted. Related captures/timelapses will have their
    camera reference set to NULL.
    """
    success, message = await view_service.delete_camera(camera_id)

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
        return templates.TemplateResponse(
            "partials/cameras/camera_not_found.html",
            {"request": request},
            status_code=404,
        )

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
