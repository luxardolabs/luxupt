"""Timelapse routes."""

from datetime import date, datetime, time, timedelta
from typing import Annotated

from db.connection import DbSession
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from logging_config import get_logger
from services.job_service import get_job_processor

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
    from web.routers.cameras_router import _resolve_protect_creds
    from protect_client import ProtectClient

    cameras = await view_service.camera_service.get_active()
    yesterday = date.today() - timedelta(days=1)
    scheduler_settings = await view_service.settings_service.get_scheduler_settings()
    global_recreate = bool(scheduler_settings.recreate_existing)

    camera_ranges: dict[str, dict[str, str | int]] = {}
    range_error: str | None = None
    if cameras:
        base_url, username, password, verify_ssl = await _resolve_protect_creds()
        if not (base_url and username and password):
            range_error = "Protect credentials are not configured."
        else:
            try:
                async with ProtectClient(
                    base_url=base_url, username=username, password=password, verify_ssl=verify_ssl
                ) as pc:
                    raw_ranges = await pc.get_all_camera_recording_ranges()
                for cam in cameras:
                    if cam.camera_id in raw_ranges:
                        oldest, newest = raw_ranges[cam.camera_id]
                        oldest_d = oldest.date()
                        newest_d = newest.date()
                        # Default to yesterday if it's in the range, else clamp to range
                        default_d = min(newest_d, date.today() - timedelta(days=1))
                        if default_d < oldest_d:
                            default_d = oldest_d
                        camera_ranges[cam.camera_id] = {
                            "name": cam.name,
                            "oldest": oldest_d.isoformat(),
                            "newest": newest_d.isoformat(),
                            # Full datetime so the form can show users the actual hour/minute
                            # bounds — Protect's recordingStart isn't midnight; pretending it
                            # was the whole day let users pick ranges that hit 404 storms.
                            "oldest_full": oldest.strftime("%Y-%m-%d %H:%M"),
                            "newest_full": newest.strftime("%Y-%m-%d %H:%M"),
                            "default": default_d.isoformat(),
                            "days": (newest_d - oldest_d).days,
                        }
            except Exception as e:
                logger.warning("Bootstrap fetch for recording ranges failed", extra={"error": str(e)})
                range_error = f"Could not read recording ranges: {str(e)[:200]}"

    # Union range for the date input bounds (oldest of all, newest of all)
    union_oldest: str | None = None
    union_newest: str | None = None
    default_date: str | None = None
    if camera_ranges:
        union_oldest = min(r["oldest"] for r in camera_ranges.values())  # type: ignore[type-var]
        union_newest = max(r["newest"] for r in camera_ranges.values())  # type: ignore[type-var]
        # Default to yesterday if it's within union, else the newest
        default_d = min(date.fromisoformat(union_newest), date.today() - timedelta(days=1))  # type: ignore[arg-type]
        if default_d < date.fromisoformat(union_oldest):  # type: ignore[arg-type]
            default_d = date.fromisoformat(union_oldest)  # type: ignore[arg-type]
        default_date = default_d.isoformat()

    return templates.TemplateResponse(
        "partials/timelapses/historical_panel.html",
        {
            "request": request,
            "user": user,
            "cameras": cameras,
            "camera_ranges": camera_ranges,
            "range_error": range_error,
            "union_oldest": union_oldest,
            "union_newest": union_newest,
            "default_date": default_date,
            "yesterday_iso": yesterday.isoformat(),
            "global_recreate": global_recreate,
        },
    )


@router.post("/historical", response_class=HTMLResponse)
async def create_historical_timelapse(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    db: DbSession,
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
    try:
        interval_int = int(interval)
        if interval_int < 5 or interval_int > 86400:
            return templates.TemplateResponse(
                "partials/timelapses/create_result.html",
                {"request": request, "success": False, "error": "Interval must be between 5 and 86400 seconds."},
            )
        start_d = date.fromisoformat(start_date)
        end_d = date.fromisoformat(end_date)
        start_t = time.fromisoformat(start_time)
        end_t = time.fromisoformat(end_time)

        if end_t <= start_t:
            return templates.TemplateResponse(
                "partials/timelapses/create_result.html",
                {"request": request, "success": False, "error": "End time must be after start time."},
            )
        if end_d < start_d:
            return templates.TemplateResponse(
                "partials/timelapses/create_result.html",
                {"request": request, "success": False, "error": "End date must be on or after start date."},
            )
        # Recording-write lag: Protect needs ~60s before a frame is in the recording stream.
        # If the end date is in the future entirely, reject. If it's today (or past)
        # with a time that crosses the lag boundary, clamp silently.
        now_local = datetime.now().astimezone()
        if end_d > now_local.date():
            return templates.TemplateResponse(
                "partials/timelapses/create_result.html",
                {"request": request, "success": False, "error": "End date cannot be in the future."},
            )
        min_lag_threshold = now_local - timedelta(seconds=60)
        # Build a per-day end_at_for helper: returns the effective end datetime for a given day
        def _end_at_for(day: date) -> datetime:
            candidate = datetime.combine(day, end_t).astimezone()
            return min(candidate, min_lag_threshold) if day == now_local.date() else candidate

        # If end_t for the actual end_d already lands in the past, fine. If end_d is today
        # and end_t pushes into the future, _end_at_for will clamp to min_lag_threshold.
        if _end_at_for(end_d) <= datetime.combine(start_d, start_t).astimezone():
            return templates.TemplateResponse(
                "partials/timelapses/create_result.html",
                {
                    "request": request,
                    "success": False,
                    "error": "End is at or before start after applying recording-lag clamp. Wait a minute or pick an earlier end time.",
                },
            )

        camera_info = await view_service.get_camera_info(camera)
        if not camera_info:
            return templates.TemplateResponse(
                "partials/timelapses/create_result.html",
                {"request": request, "success": False, "error": "Camera not found."},
            )
        camera_safe_name = camera_info["safe_name"]
        keep = (keep_images == "true")
        force_recreate = (recreate_existing == "true")

        created_jobs: list[str] = []
        skipped_days: list[str] = []  # for reporting
        recreated_jobs: list[str] = []  # job_ids we cancelled+deleted to re-run

        if output_mode == "combined":
            from sqlalchemy import select
            from models.job import Job, JobStatus
            stmt = select(Job).where(
                Job.camera_safe_name == camera_safe_name,
                Job.interval == interval_int,
                Job.job_type == "historical_combined",
                Job.start_at == datetime.combine(start_d, start_t).astimezone(),
                Job.end_at >= _end_at_for(end_d) - timedelta(seconds=120),
                Job.end_at <= _end_at_for(end_d) + timedelta(seconds=120),
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()
            if existing is not None:
                if not force_recreate:
                    return templates.TemplateResponse(
                        "partials/timelapses/create_result.html",
                        {
                            "request": request,
                            "success": False,
                            "error": f"A combined job for {camera_safe_name} over this range is already running (id {existing.job_id[:8]}). Toggle 'Recreate existing' to replace it.",
                        },
                    )
                # Recreate: cancel + delete the existing job before creating the new one
                await view_service.job_service.cancel_job(existing.job_id)
                await view_service.job_service.delete_job(existing.job_id)
                await db.commit()
                recreated_jobs.append(existing.job_id)

            # One job spanning the full range
            start_at = datetime.combine(start_d, start_t).astimezone()
            end_at = _end_at_for(end_d)
            range_label = f"{start_d.isoformat()}_to_{end_d.isoformat()}"
            title = f"{camera_safe_name}_{range_label}_{interval_int}s_historical_combined"
            job = await view_service.job_service.create(
                title=title,
                camera_safe_name=camera_safe_name,
                camera_id=camera_info["camera_id"],
                target_date=start_d,  # earliest date for the existing target_date column
                interval=interval_int,
                keep_images=keep,
                job_type="historical_combined",
                start_at=start_at,
                end_at=end_at,
                daily_window_start=start_t,
                daily_window_end=end_t,
            )
            await db.commit()
            created_jobs.append(job.job_id)
            get_job_processor().start_job(job.job_id, start_d.isoformat(), camera_safe_name, interval_int)
        else:
            # Fan out one job per day in the range
            day = start_d
            while day <= end_d:
                date_str = day.isoformat()
                if await view_service.check_job_exists(camera_safe_name, date_str, interval_int):
                    if not force_recreate:
                        skipped_days.append(date_str)
                        day += timedelta(days=1)
                        continue
                    # Find the existing job for this camera/date/interval and remove it
                    existing_job = await view_service.job_service.exists_for_camera_date(
                        camera_safe_name, day, interval_int
                    )
                    if existing_job is not None:
                        await view_service.job_service.cancel_job(existing_job.job_id)
                        await view_service.job_service.delete_job(existing_job.job_id)
                        await db.commit()
                        recreated_jobs.append(existing_job.job_id)
                start_at = datetime.combine(day, start_t).astimezone()
                end_at = _end_at_for(day)
                # Skip days whose end clamps to before/equal-to start (e.g., today before 00:01)
                if end_at <= start_at:
                    day += timedelta(days=1)
                    continue
                title = f"{camera_safe_name}_{date_str}_{interval_int}s_historical"
                job = await view_service.job_service.create(
                    title=title,
                    camera_safe_name=camera_safe_name,
                    camera_id=camera_info["camera_id"],
                    target_date=day,
                    interval=interval_int,
                    keep_images=keep,
                    job_type="historical",
                    start_at=start_at,
                    end_at=end_at,
                    daily_window_start=start_t,
                    daily_window_end=end_t,
                )
                await db.commit()
                created_jobs.append(job.job_id)
                get_job_processor().start_job(job.job_id, date_str, camera_safe_name, interval_int)
                day += timedelta(days=1)

        if not created_jobs:
            err = f"All matching jobs for {camera_safe_name} in this range already exist."
            if skipped_days:
                err += f" Skipped: {', '.join(skipped_days)}. Toggle 'Recreate existing' to replace them."
            return templates.TemplateResponse(
                "partials/timelapses/create_result.html",
                {"request": request, "success": False, "error": err},
            )

        # Build a human-readable summary
        if output_mode == "combined":
            date_summary = f"{start_date} → {end_date}"
        else:
            date_summary = f"{start_date} → {end_date} ({len(created_jobs)} jobs)"
            if skipped_days:
                date_summary += f", skipped {len(skipped_days)} existing"
            if recreated_jobs:
                date_summary += f", replaced {len(recreated_jobs)}"

        return templates.TemplateResponse(
            "partials/timelapses/create_result.html",
            {
                "request": request,
                "success": True,
                "job_id": created_jobs[0] if len(created_jobs) == 1 else None,
                "camera": camera_safe_name,
                "date": date_summary,
                "interval": interval_int,
                "skipped_days": skipped_days,
                "recreated_jobs": recreated_jobs,
            },
        )
    except Exception as e:
        logger.error("Error creating historical timelapse", extra={"error": str(e), "type": type(e).__name__})
        return templates.TemplateResponse(
            "partials/timelapses/create_result.html",
            {"request": request, "success": False, "error": f"{type(e).__name__}: {str(e)[:200]}"},
        )


@router.post("/create", response_class=HTMLResponse)
async def create_timelapse(
    request: Request,
    templates: TemplatesDep,
    view_service: TimelapsesViewDep,
    db: DbSession,
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
        interval_int = int(interval)

        # Look up camera to get safe_name (camera param is now camera_id)
        camera_info = await view_service.get_camera_info(camera)
        if not camera_info:
            return templates.TemplateResponse(
                "partials/timelapses/create_result.html",
                {
                    "request": request,
                    "success": False,
                    "error": "Camera not found",
                },
            )

        camera_safe_name = camera_info["safe_name"]

        # Check if job already exists (use safe_name for job lookup since jobs use file paths)
        if await view_service.check_job_exists(camera_safe_name, date_str, interval_int):
            return templates.TemplateResponse(
                "partials/timelapses/create_result.html",
                {
                    "request": request,
                    "success": False,
                    "error": f"Job already exists for {camera_safe_name} on {date_str} at {interval_int}s interval",
                },
            )

        # Create job in database (use safe_name for file paths, camera_id for DB references)
        title = f"{camera_safe_name}_{date_str}_{interval_int}s"
        job = await view_service.create_job(
            title=title,
            camera_safe_name=camera_safe_name,
            camera_id=camera_info["camera_id"],
            date_str=date_str,
            interval=interval_int,
        )
        await db.commit()

        # Start processing the job in background (use safe_name for file paths)
        get_job_processor().start_job(job.job_id, date_str, camera_safe_name, interval_int)

        return templates.TemplateResponse(
            "partials/timelapses/create_result.html",
            {
                "request": request,
                "success": True,
                "job_id": job.job_id,
                "camera": camera_safe_name,
                "date": date_str,
                "interval": interval_int,
            },
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
    db: DbSession,
    user: str = Depends(get_current_user),
) -> Response:
    """Delete/cancel a job."""
    success, action = await view_service.cancel_or_delete_job(job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Job not found")

    await db.commit()
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
    db: DbSession,
    user: str = Depends(get_current_user),
) -> Response:
    """Mark all stale running/pending jobs as failed."""
    count = await view_service.cleanup_stale_jobs()
    await db.commit()

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
    db: DbSession,
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
        # Convert checkbox "on" value to bool (checkbox is present = enabled)
        is_enabled = enabled is not None
        should_keep_images = keep_images is not None
        should_recreate_existing = recreate_existing is not None

        # Convert interval strings to integers
        intervals_list = None
        if enabled_intervals:
            intervals_list = [int(i) for i in enabled_intervals]

        # Convert run_time string from form to time object
        run_time_obj = datetime.strptime(run_time, "%H:%M").time()

        # Update settings
        update_data = {
            "enabled": is_enabled,
            "run_time": run_time_obj,
            "days_ago": days_ago,
            "concurrent_jobs": concurrent_jobs,
            "keep_images": should_keep_images,
            "recreate_existing": should_recreate_existing,
            "enabled_cameras": enabled_cameras if enabled_cameras else None,
            "enabled_intervals": intervals_list,
            # FFmpeg settings (None means use env var defaults)
            "frame_rate": frame_rate if frame_rate else None,
            "crf": crf if crf is not None else None,  # crf=0 is valid
            "preset": preset if preset else None,
            "pixel_format": pixel_format if pixel_format else None,
            "ffmpeg_timeout": ffmpeg_timeout if ffmpeg_timeout else None,
        }

        await view_service.update_scheduler_settings(update_data)
        await db.commit()

        return templates.TemplateResponse(
            "partials/timelapses/scheduler_result.html",
            {
                "request": request,
                "success": True,
                "enabled": is_enabled,
                "run_time": run_time_obj,
            },
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
    db: DbSession,
    user: str = Depends(get_current_user),
) -> Response:
    """Delete a timelapse (database record and files)."""
    success = await view_service.delete_timelapse(timelapse_id)

    if not success:
        raise HTTPException(status_code=404, detail="Timelapse not found")

    await db.commit()

    # Return OOB update to refresh the stats cards
    context = await view_service.get_stats_context()
    return templates.TemplateResponse(
        "partials/timelapses/stats_oob.html",
        {"request": request, **context},
    )
