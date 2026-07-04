"""Image browser routes."""

import asyncio
from datetime import date

import config
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from services.image_service import image_service
from utils import async_fs

from web.auth import get_current_user
from web.deps import ImagesViewDep, TemplatesDep

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
        "pages/images.html",
        {"request": request, "user": user, **context},
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
        "partials/images/image_grid.html",
        {"request": request, **context},
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
        "partials/images/image_filters.html",
        {
            "request": request,
            "cameras": context["cameras"],
            "available_dates": context["available_dates"],
            "available_intervals": context["available_intervals"],
            "filters": context["filters"],
        },
    )


@router.get("/camera/{camera_safe_name}", response_class=HTMLResponse)
async def camera_images_page(
    camera_safe_name: str,
    request: Request,
    templates: TemplatesDep,
    view_service: ImagesViewDep,
    date_str: str | None = Query(None, alias="date"),
    interval: str | None = Query(None),
    page: int = Query(1, ge=1),
    user: str = Depends(get_current_user),
) -> Response:
    """Render images page for a specific camera."""
    # Handle empty strings from form
    interval_int = int(interval) if interval else None
    date_str = date_str if date_str else None

    context = await view_service.get_camera_images_context(
        camera_safe_name,
        date_str=date_str,
        interval=interval_int,
        page=page,
    )

    if context["camera"] is None:
        return templates.TemplateResponse(
            "pages/404.html",
            {"request": request, "message": "Camera not found"},
            status_code=404,
        )

    return templates.TemplateResponse(
        "pages/camera_images.html",
        {"request": request, "user": user, **context},
    )


@router.get("/latest", response_class=HTMLResponse)
async def latest_images_page(
    request: Request,
    templates: TemplatesDep,
    view_service: ImagesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the latest images page."""
    context = await view_service.get_latest_images_context()

    return templates.TemplateResponse(
        "pages/latest_images.html",
        {"request": request, "user": user, **context},
    )


# =============================================================================
# Lightbox endpoint
# =============================================================================


@router.get("/lightbox/{camera}/{timestamp}", response_class=HTMLResponse)
async def image_lightbox(
    camera: str,
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
        camera_safe_name=camera,
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
        "partials/images/lightbox.html",
        {"request": request, **context},
    )


@router.get("/lightbox/{camera}/{timestamp}/content", response_class=HTMLResponse)
async def image_lightbox_content(
    camera: str,
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
        camera_safe_name=camera,
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
        "partials/images/lightbox_content.html",
        {"request": request, **context},
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
    size: int | None = Query(None, ge=50, le=1024),
    _user: str = Depends(get_current_user),
) -> Response:
    """Serve thumbnail for an image.

    Thumbnails are created during image fetch, so just build the path and serve.
    All path info is in the URL - no DB query needed.
    """
    if size is None:
        size = config.THUMBNAIL_SIZE_DEFAULT

    thumb_path = image_service.build_thumbnail_path(camera, interval, capture_date, timestamp, size)

    if not await async_fs.path_exists(thumb_path):
        # Thumbnail may still be in the generation queue — wait briefly
        for _ in range(40):
            await asyncio.sleep(0.05)
            if await async_fs.path_exists(thumb_path):
                break
        else:
            raise HTTPException(status_code=404, detail="Thumbnail not found")

    return FileResponse(
        thumb_path,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=86400"},
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
        "partials/images/delete_panel.html",
        {"request": request, "user": user, **context},
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
        "partials/images/delete_preview.html",
        {"request": request, **context},
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
        "partials/images/delete_result.html",
        {"request": request, "result": result, **context},
    )
