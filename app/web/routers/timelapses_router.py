"""Timelapse routes."""

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

from app.logging_config import get_logger
from app.web.auth import get_current_user
from app.web.deps import TemplatesDep, TimelapsesViewDep
from app.web.query_params import TimelapseStatusFilter

logger = get_logger(__name__)

router = APIRouter(tags=["timelapses"])


@router.get("", response_class=HTMLResponse)
async def timelapses_page(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    camera: str | None = Query(None),
    date_str: str | None = Query(None, alias="date"),
    interval: str | None = Query(None),
    status: TimelapseStatusFilter = None,
    page: int = Query(1, ge=1),
    user: str = Depends(get_current_user),
) -> Response:
    """Render the timelapses browser page."""
    # Handle empty strings from form
    interval_int = int(interval) if interval else None

    context = await view_service.get_browser_context(
        camera=camera if camera else None,
        date_str=date_str if date_str else None,
        interval=interval_int,
        status=status if status else None,
        page=page,
    )

    return templates.TemplateResponse(
        request,
        "pages/timelapses.html",
        {"user": user, **context},
    )


@router.get("/create", response_class=HTMLResponse)
async def create_timelapse_panel(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    camera: str | None = Query(None),
    user: str = Depends(get_current_user),
) -> Response:
    """Render the create timelapse panel (loaded via HTMX)."""
    context = await view_service.get_create_timelapse_context(camera=camera)

    return templates.TemplateResponse(
        request,
        "partials/timelapses/create_panel.html",
        {"user": user, **context},
    )


@router.get("/historical", response_class=HTMLResponse)
async def historical_timelapse_panel(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the historical-timelapse creation panel.

    One bootstrap call to Protect populates the recording ranges for ALL
    cameras at render time. The template displays a per-camera summary so
    the operator can see what dates are available for each camera before
    submitting a job.
    """
    context = await view_service.get_historical_panel_context()

    return templates.TemplateResponse(
        request,
        "partials/timelapses/historical_panel.html",
        {"user": user, **context},
    )


@router.post("/historical", response_class=HTMLResponse)
async def create_historical_timelapse(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    camera_id: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    interval: str = Form(...),
    output_mode: str = Form(default="per_day"),  # per_day | combined
    keep_images: str | None = Form(default=None),
    recreate_existing: str | None = Form(default=None),
    user: str = Depends(get_current_user),
) -> Response:
    """Create one or more historical timelapse jobs.

    output_mode='per_day' creates one job per day in the range (each becomes its
    own daily MP4 via the existing assembly).

    output_mode='combined' creates one job spanning the full range — the
    JobProcessor will route it to the combined-assembly path which globs frames
    across all day directories.
    """
    context = await view_service.create_historical_jobs(
        camera_id=camera_id,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        interval=interval,
        output_mode=output_mode,
        keep_images=keep_images,
        recreate_existing=recreate_existing,
    )
    return templates.TemplateResponse(
        request,
        "partials/timelapses/create_result.html",
        {**context},
    )


@router.post("/create", response_class=HTMLResponse)
async def create_timelapse(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    camera_id: str = Form(...),
    date_str: str = Form(..., alias="date"),
    interval: str = Form(...),
    user: str = Depends(get_current_user),
) -> Response:
    """Create a new timelapse job (HTMX).

    The camera parameter is the camera_id (UUID) from the dropdown.
    We look up the camera to get safe_name for file paths.
    """
    context = await view_service.create_and_start_job(
        camera_id=camera_id,
        date_str=date_str,
        interval=int(interval),
    )
    return templates.TemplateResponse(
        request,
        "partials/timelapses/create_result.html",
        {**context},
    )


@router.get("/partials/dates", response_class=HTMLResponse)
async def dates_select_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    camera: str | None = Query(None),
    user: str = Depends(get_current_user),
) -> Response:
    """Return date select options based on camera selection."""
    context = await view_service.get_dates_context(camera=camera)

    return templates.TemplateResponse(
        request,
        "partials/timelapses/date_select.html",
        {**context},
    )


@router.get("/partials/intervals", response_class=HTMLResponse)
async def intervals_select_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    camera: str | None = Query(None),
    date_str: str | None = Query(None, alias="date"),
    user: str = Depends(get_current_user),
) -> Response:
    """Return interval select options based on camera and date selection."""
    context = await view_service.get_intervals_context(camera=camera, date_str=date_str)

    return templates.TemplateResponse(
        request,
        "partials/timelapses/interval_select.html",
        {**context},
    )


@router.get("/partials/preview", response_class=HTMLResponse)
async def preview_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    camera: str | None = Query(None),
    date_str: str | None = Query(None, alias="date"),
    interval: str | None = Query(None),
    user: str = Depends(get_current_user),
) -> Response:
    """Return preview of timelapse to be created."""
    interval_int = int(interval) if interval else None

    context = await view_service.get_preview_context(
        camera=camera,
        date_str=date_str,
        interval=interval_int,
    )

    return templates.TemplateResponse(
        request,
        "partials/timelapses/preview.html",
        {**context},
    )


@router.get("/partials/list", response_class=HTMLResponse)
async def timelapse_list_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    camera: str | None = Query(None),
    date_str: str | None = Query(None, alias="date"),
    interval: str | None = Query(None),
    status: TimelapseStatusFilter = None,
    page: int = Query(1, ge=1),
    user: str = Depends(get_current_user),
) -> Response:
    """Render timelapse list partial for HTMX updates."""
    # Handle empty strings from form
    interval_int = int(interval) if interval else None

    context = await view_service.get_browser_context(
        camera=camera if camera else None,
        date_str=date_str if date_str else None,
        interval=interval_int,
        status=status if status else None,
        page=page,
    )

    return templates.TemplateResponse(
        request,
        "partials/timelapses/timelapse_list.html",
        {**context},
    )


@router.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render stats partial for HTMX polling."""
    context = await view_service.get_stats_context()

    return templates.TemplateResponse(
        request,
        "partials/timelapses/stats.html",
        {**context},
    )


@router.get("/{timelapse_id}/lightbox", response_class=HTMLResponse)
async def timelapse_lightbox(
    timelapse_id: int,
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render timelapse lightbox for video viewing."""
    context = await view_service.get_lightbox_context(timelapse_id)

    if not context["timelapse"]:
        raise HTTPException(status_code=404, detail="Timelapse not found")

    return templates.TemplateResponse(
        request,
        "partials/timelapses/lightbox.html",
        {**context},
    )


@router.get("/{timelapse_id}/video")
async def serve_timelapse_video(
    timelapse_id: int,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Serve timelapse video file."""
    file_path, filename = await view_service.get_video_path(timelapse_id)

    if not file_path:
        raise HTTPException(status_code=404, detail="Timelapse not found")

    return FileResponse(
        file_path,
        media_type="video/mp4",
        filename=filename,
    )


@router.get("/{timelapse_id}/thumbnail")
async def serve_timelapse_thumbnail(
    timelapse_id: int,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Serve timelapse thumbnail image."""
    return await view_service.serve_thumbnail(timelapse_id)


@router.delete("/{timelapse_id}", response_class=HTMLResponse)
async def delete_timelapse(
    timelapse_id: int,
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Delete a timelapse (database record and files)."""
    # Deletion + the OOB stats context the fragment re-renders are one view operation.
    context = await view_service.delete_timelapse_and_build_stats(timelapse_id)
    return templates.TemplateResponse(
        request,
        "partials/timelapses/stats_oob.html",
        {**context},
    )
