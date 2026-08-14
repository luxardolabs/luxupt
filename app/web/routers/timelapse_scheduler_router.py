"""Timelapse scheduler routes — the automation-config domain (panel + save).

Split out of timelapses_router.py by domain: the scheduler is the daily-automation settings,
distinct from timelapse artifacts and from the job lifecycle. Mounted under /timelapses.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse

from app.logging_config import get_logger
from app.web.auth import get_current_user
from app.web.deps import TemplatesDep, TimelapsesViewDep

logger = get_logger(__name__)

router = APIRouter(tags=["timelapses"])


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
        request,
        "partials/timelapses/scheduler_panel.html",
        {**context},
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
            request,
            "partials/timelapses/scheduler_result.html",
            {**context},
        )
    except Exception as e:
        logger.error("Error saving scheduler settings", extra={"error": str(e)})
        return templates.TemplateResponse(
            request,
            "partials/timelapses/scheduler_result.html",
            {
                "success": False,
                "error": "Failed to save scheduler settings. Check server logs for details.",
            },
        )
