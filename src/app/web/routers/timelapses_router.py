"""Timelapse routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from logging_config import get_logger

from web.auth import get_current_user
from web.deps import TemplatesDep, TimelapsesViewDep

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
    status: str | None = Query(None),
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
        "pages/timelapses.html",
        {"request": request, "user": user, **context},
    )


@router.get("/jobs", response_class=HTMLResponse)
async def timelapses_jobs_page(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the timelapse jobs page."""
    # Get stats and jobs context
    stats_context = await view_service.get_stats_context()
    jobs_context = await view_service.get_jobs_context()

    return templates.TemplateResponse(
        "pages/timelapses_jobs.html",
        {"request": request, "user": user, **stats_context, **jobs_context},
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
        "partials/timelapses/create_panel.html",
        {"request": request, "user": user, **context},
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
        "partials/timelapses/historical_panel.html",
        {"request": request, "user": user, **context},
    )


@router.post("/historical", response_class=HTMLResponse)
async def create_historical_timelapse(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    camera: str = Form(...),  # camera_id
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
        camera_id=camera,
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
        "partials/timelapses/create_result.html",
        {"request": request, **context},
    )


@router.post("/create", response_class=HTMLResponse)
async def create_timelapse(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    camera: str = Form(...),  # This is now camera_id from the dropdown
    date_str: str = Form(..., alias="date"),
    interval: str = Form(...),
    user: str = Depends(get_current_user),
) -> Response:
    """Create a new timelapse job (HTMX).

    The camera parameter is the camera_id (UUID) from the dropdown.
    We look up the camera to get safe_name for file paths.
    """
    try:
        context = await view_service.create_and_start_job(
            camera_id=camera,
            date_str=date_str,
            interval=int(interval),
        )
        return templates.TemplateResponse(
            "partials/timelapses/create_result.html",
            {"request": request, **context},
        )
    except Exception as e:
        logger.error("Error creating timelapse", extra={"error": str(e)})
        return templates.TemplateResponse(
            "partials/timelapses/create_result.html",
            {
                "request": request,
                "success": False,
                "error": "Failed to create timelapse. Check server logs for details.",
            },
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
        "partials/timelapses/date_select.html",
        {"request": request, **context},
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
        "partials/timelapses/interval_select.html",
        {"request": request, **context},
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
        "partials/timelapses/preview.html",
        {"request": request, **context},
    )


@router.get("/partials/list", response_class=HTMLResponse)
async def timelapse_list_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    camera: str | None = Query(None),
    date_str: str | None = Query(None, alias="date"),
    interval: str | None = Query(None),
    status: str | None = Query(None),
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
        "partials/timelapses/timelapse_list.html",
        {"request": request, **context},
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
        "partials/timelapses/stats.html",
        {"request": request, **context},
    )


@router.get("/partials/jobs", response_class=HTMLResponse)
async def jobs_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render jobs panel partial for HTMX polling."""
    context = await view_service.get_jobs_context()

    return templates.TemplateResponse(
        "partials/timelapses/job_list.html",
        {"request": request, **context},
    )


@router.get("/partials/completed", response_class=HTMLResponse)
async def completed_jobs_partial(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render recently completed jobs partial for HTMX polling."""
    completed_jobs = await view_service.job_service.get_completed(limit=8)

    return templates.TemplateResponse(
        "partials/timelapses/recently_completed.html",
        {"request": request, "completed_jobs": completed_jobs},
    )


@router.get("/partials/job/{job_id}", response_class=HTMLResponse)
async def job_progress_partial(
    job_id: str,
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render single job progress partial for HTMX polling."""
    context = await view_service.get_job_context(job_id)

    if not context["job"]:
        return templates.TemplateResponse(
            "partials/timelapses/job_not_found.html",
            {"request": request, "job_id": job_id},
        )

    response = templates.TemplateResponse(
        "partials/timelapses/job_progress.html",
        {"request": request, **context},
    )

    # Trigger parent refresh when job completes or fails
    job = context["job"]
    if job.status in ["completed", "failed"]:
        response.headers["HX-Trigger"] = "job-finished"

    return response


@router.delete("/job/{job_id}", response_class=HTMLResponse)
async def delete_job(
    job_id: str,
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Delete/cancel a job."""
    success, action = await view_service.cancel_or_delete_job(job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Job not found")

    logger.info("Job action completed", extra={"job_id": job_id, "action": action})

    # Return refreshed job list via OOB swap to update counts and bring in next items
    jobs_context = await view_service.get_jobs_context()
    return templates.TemplateResponse(
        "partials/timelapses/job_list.html",
        {"request": request, **jobs_context},
        headers={
            "HX-Reswap": "innerHTML",
            "HX-Retarget": "#active-jobs",
            "HX-Trigger": "job-finished",
        },
    )


@router.post("/jobs/cleanup-stale", response_class=HTMLResponse)
async def cleanup_stale_jobs(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Mark all stale running/pending jobs as failed."""
    count = await view_service.cleanup_stale_jobs()

    logger.info("Cleaned up stale jobs", extra={"count": count})

    # Return updated job list
    context = await view_service.get_jobs_context()
    return templates.TemplateResponse(
        "partials/timelapses/job_list.html",
        {"request": request, **context},
    )


@router.get("/scheduler", response_class=HTMLResponse)
async def scheduler_panel(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Render the scheduler settings panel (loaded via HTMX)."""
    context = await view_service.get_scheduler_context()

    return templates.TemplateResponse(
        "partials/timelapses/scheduler_panel.html",
        {"request": request, **context},
    )


@router.post("/scheduler", response_class=HTMLResponse)
async def save_scheduler_settings(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    enabled: Annotated[str | None, Form()] = None,
    run_time: str = Form("01:00"),
    days_ago: int = Form(1),
    concurrent_jobs: int = Form(2),
    keep_images: Annotated[str | None, Form()] = None,
    recreate_existing: Annotated[str | None, Form()] = None,
    enabled_cameras: Annotated[list[str] | None, Form()] = None,
    enabled_intervals: Annotated[list[str] | None, Form()] = None,
    # FFmpeg settings
    frame_rate: Annotated[int | None, Form()] = None,
    crf: Annotated[int | None, Form()] = None,
    preset: Annotated[str | None, Form()] = None,
    pixel_format: Annotated[str | None, Form()] = None,
    ffmpeg_timeout: Annotated[int | None, Form()] = None,
    user: str = Depends(get_current_user),
) -> Response:
    """Save scheduler settings (HTMX)."""
    try:
        context = await view_service.save_scheduler_settings(
            enabled=enabled,
            run_time=run_time,
            days_ago=days_ago,
            concurrent_jobs=concurrent_jobs,
            keep_images=keep_images,
            recreate_existing=recreate_existing,
            enabled_cameras=enabled_cameras,
            enabled_intervals=enabled_intervals,
            frame_rate=frame_rate,
            crf=crf,
            preset=preset,
            pixel_format=pixel_format,
            ffmpeg_timeout=ffmpeg_timeout,
        )

        return templates.TemplateResponse(
            "partials/timelapses/scheduler_result.html",
            {"request": request, **context},
        )
    except Exception as e:
        logger.error("Error saving scheduler settings", extra={"error": str(e)})
        return templates.TemplateResponse(
            "partials/timelapses/scheduler_result.html",
            {
                "request": request,
                "success": False,
                "error": "Failed to save scheduler settings. Check server logs for details.",
            },
        )


@router.get("/camera/{camera_safe_name}", response_class=HTMLResponse)
async def camera_timelapses_page(
    camera_safe_name: str,
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    page: int = Query(1, ge=1),
    user: str = Depends(get_current_user),
) -> Response:
    """Render timelapses page for a specific camera."""
    context = await view_service.get_camera_timelapses_context(
        camera_safe_name,
        page=page,
    )

    if context["camera"] is None:
        return templates.TemplateResponse(
            "pages/404.html",
            {"request": request, "message": "Camera not found"},
            status_code=404,
        )

    return templates.TemplateResponse(
        "pages/camera_timelapses.html",
        {"request": request, "user": user, **context},
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
        "partials/timelapses/lightbox.html",
        {"request": request, **context},
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
    thumb_path = await view_service.get_thumbnail_path(timelapse_id)

    if not thumb_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return FileResponse(thumb_path, media_type="image/jpeg")


@router.delete("/{timelapse_id}", response_class=HTMLResponse)
async def delete_timelapse(
    timelapse_id: int,
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    user: str = Depends(get_current_user),
) -> Response:
    """Delete a timelapse (database record and files)."""
    success = await view_service.delete_timelapse(timelapse_id)

    if not success:
        raise HTTPException(status_code=404, detail="Timelapse not found")


    # Return OOB update to refresh the stats cards
    context = await view_service.get_stats_context()
    return templates.TemplateResponse(
        "partials/timelapses/stats_oob.html",
        {"request": request, **context},
    )
