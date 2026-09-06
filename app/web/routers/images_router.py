"""Image browser routes."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

from app import config
from app.web.auth import get_current_user
from app.web.deps import ImagesViewDep, TemplatesDep
from app.web.query_params import ThumbnailSizeFilter

router = APIRouter(tags=["images"])


@router.get("", response_class=HTMLResponse)
async def images_page(
    request: Request,
    templates: TemplatesDep,
    view_service: ImagesViewDep,
    camera: str | None = Query(None),
    date_str: str | None = Query(None, alias="date"),
    interval: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(36, ge=36, le=108),
    user: str = Depends(get_current_user),
) -> Response:
    """Render the image browser page."""
    # Parse interval (handle empty strings from form)
    interval_int = int(interval) if interval else None
    # Handle empty strings
    camera = camera if camera else None
    date_str = date_str if date_str else None

    context = await view_service.get_browser_context(
        camera=camera,
        date_str=date_str,
        interval=interval_int,
        page=page,
        per_page=per_page,
    )

    return templates.TemplateResponse(
        request,
        "pages/images.html",
        {"user": user, **context},
    )


@router.get("/partials/grid", response_class=HTMLResponse)
async def image_grid_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: ImagesViewDep,
    camera: str | None = Query(None),
    date_str: str | None = Query(None, alias="date"),
    interval: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(36, ge=36, le=108),
    user: str = Depends(get_current_user),
) -> Response:
    """Render image grid partial for HTMX updates."""
    # Handle empty strings from form
    interval_int = int(interval) if interval else None
    camera = camera if camera else None
    date_str = date_str if date_str else None

    context = await view_service.get_image_grid_context(
        camera=camera,
        date_str=date_str,
        interval=interval_int,
        page=page,
        per_page=per_page,
    )

    return templates.TemplateResponse(
        request,
        "partials/images/image_grid.html",
        {**context},
    )


@router.get("/partials/filters", response_class=HTMLResponse)
async def image_filters_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: ImagesViewDep,
    camera: str | None = Query(None),
    user: str = Depends(get_current_user),
) -> Response:
    """Render image filters partial for HTMX updates."""
    # Handle empty string from form
    camera = camera if camera else None
    context = await view_service.get_browser_context(camera=camera)

    return templates.TemplateResponse(
        request,
        "partials/images/image_filters.html",
        {
            "cameras": context["cameras"],
            "available_dates": context["available_dates"],
            "available_intervals": context["available_intervals"],
            "filters": context["filters"],
        },
    )


# =============================================================================
# Lightbox endpoint
# =============================================================================


@router.get("/lightbox/{camera_safe_name}/{timestamp}", response_class=HTMLResponse)
async def image_lightbox(
    camera_safe_name: str,
    timestamp: int,
    request: Request,
    templates: TemplatesDep,
    view_service: ImagesViewDep,
    filter_camera: str | None = Query(None, alias="camera"),
    date_str: str | None = Query(None, alias="date"),
    interval: str | None = Query(None),
    user: str = Depends(get_current_user),
) -> Response:
    """Render lightbox partial for an image with prev/next navigation."""
    # Handle empty strings from form
    interval_int = int(interval) if interval else None
    filter_camera = filter_camera if filter_camera else None
    date_str = date_str if date_str else None

    context = await view_service.get_lightbox_context(
        camera_safe_name=camera_safe_name,
        timestamp=timestamp,
        filter_camera=filter_camera,
        date_str=date_str,
        interval=interval_int,
    )

    if not context.get("image"):
        raise HTTPException(status_code=404, detail="Image not found")

    # Build filter params for navigation links
    params = []
    if filter_camera:
        params.append(f"camera={filter_camera}")
    if date_str:
        params.append(f"date={date_str}")
    if interval:
        params.append(f"interval={interval}")
    context["filter_params"] = "&".join(params)

    return templates.TemplateResponse(
        request,
        "partials/images/lightbox.html",
        {**context},
    )


@router.get(
    "/lightbox/{camera_safe_name}/{timestamp}/content", response_class=HTMLResponse
)
async def image_lightbox_content(
    camera_safe_name: str,
    timestamp: int,
    request: Request,
    templates: TemplatesDep,
    view_service: ImagesViewDep,
    filter_camera: str | None = Query(None, alias="camera"),
    date_str: str | None = Query(None, alias="date"),
    interval: str | None = Query(None),
    user: str = Depends(get_current_user),
) -> Response:
    """Render lightbox content only (for HTMX navigation swaps)."""
    interval_int = int(interval) if interval else None
    filter_camera = filter_camera if filter_camera else None
    date_str = date_str if date_str else None

    context = await view_service.get_lightbox_context(
        camera_safe_name=camera_safe_name,
        timestamp=timestamp,
        filter_camera=filter_camera,
        date_str=date_str,
        interval=interval_int,
    )

    if not context.get("image"):
        raise HTTPException(status_code=404, detail="Image not found")

    params = []
    if filter_camera:
        params.append(f"camera={filter_camera}")
    if date_str:
        params.append(f"date={date_str}")
    if interval:
        params.append(f"interval={interval}")
    context["filter_params"] = "&".join(params)

    return templates.TemplateResponse(
        request,
        "partials/images/lightbox_content.html",
        {**context},
    )


# =============================================================================
# Image file serving endpoints
# =============================================================================


@router.get("/file/{camera}/{interval}/{timestamp}")
async def get_image_file(
    camera: str,
    interval: int,
    timestamp: int,
    view_service: ImagesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Serve full resolution image file."""
    file_path, exists = await view_service.get_capture_path(camera, timestamp, interval)

    if not file_path:
        raise HTTPException(status_code=404, detail="Image not found")

    if not exists:
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    return FileResponse(
        file_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/thumbnail/{camera}/{interval}/{capture_date}/{timestamp}")
async def get_image_thumbnail(
    camera: str,
    interval: int,
    capture_date: date,
    timestamp: int,
    view_service: ImagesViewDep,
    size: ThumbnailSizeFilter = None,
    _user: str = Depends(get_current_user),
) -> Response:
    """Serve thumbnail for an image.

    Thumbnails are created during image fetch, so just build the path and serve.
    All path info is in the URL - no DB query needed.
    """
    if size is None:
        size = config.THUMBNAIL_SIZE_DEFAULT

    return await view_service.serve_thumbnail(
        camera, interval, capture_date, timestamp, size
    )


# =============================================================================
# Image deletion endpoints
# =============================================================================


@router.get("/delete", response_class=HTMLResponse)
async def delete_images_panel(
    request: Request,
    templates: TemplatesDep,
    view_service: ImagesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the image deletion panel."""
    context = await view_service.get_delete_panel_context()

    return templates.TemplateResponse(
        request,
        "partials/images/delete_panel.html",
        {"user": user, **context},
    )


@router.get("/delete/preview", response_class=HTMLResponse)
async def delete_images_preview(
    request: Request,
    templates: TemplatesDep,
    view_service: ImagesViewDep,
    camera: str | None = Query(None),
    date_str: str | None = Query(None, alias="date"),
    interval: str | None = Query(None),
    user: str = Depends(get_current_user),
) -> Response:
    """Get preview of images that would be deleted."""
    # Handle empty strings from form
    interval_int = int(interval) if interval else None
    camera = camera if camera else None
    date_str = date_str if date_str else None

    context = await view_service.get_deletion_preview(
        camera=camera,
        date_str=date_str,
        interval=interval_int,
    )

    return templates.TemplateResponse(
        request,
        "partials/images/delete_preview.html",
        {**context},
    )


@router.delete("/delete", response_class=HTMLResponse)
async def delete_images(
    request: Request,
    templates: TemplatesDep,
    view_service: ImagesViewDep,
    camera: str | None = Query(None),
    date_str: str | None = Query(None, alias="date"),
    interval: str | None = Query(None),
    user: str = Depends(get_current_user),
) -> Response:
    """Delete images matching filters."""
    # Handle empty strings from form
    interval_int = int(interval) if interval else None
    camera = camera if camera else None
    date_str = date_str if date_str else None

    result = await view_service.delete_images(
        camera=camera,
        date_str=date_str,
        interval=interval_int,
    )

    # Get fresh filter options (dates/cameras may have changed)
    context = await view_service.get_delete_panel_context()

    return templates.TemplateResponse(
        request,
        "partials/images/delete_result.html",
        {"result": result, **context},
    )
