"""Timelapse job routes — the JobProcessor lifecycle (list, progress, cancel, cleanup).

Split out of timelapses_router.py by domain: the jobs concern is backed by the JobProcessor /
JobCoreService, distinct from the timelapse-artifact browsing/serving routes. Mounted under the
same /timelapses prefix. Safe alongside the artifact router's int-typed /{timelapse_id} catch-all
(the /jobs and /job/{job_id} paths are non-numeric / multi-segment, so they never collide).
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from app.logging_config import get_logger
from app.web.auth import get_current_user
from app.web.deps import TemplatesDep, TimelapsesViewDep

logger = get_logger(__name__)

router = APIRouter(tags=["timelapses"])


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
        request,
        "pages/timelapses_jobs.html",
        {"user": user, **stats_context, **jobs_context},
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
        request,
        "partials/timelapses/job_list.html",
        {**context},
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
        request,
        "partials/timelapses/recently_completed.html",
        {"completed_jobs": completed_jobs},
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
            request,
            "partials/timelapses/job_not_found.html",
            {"job_id": job_id},
        )

    response = templates.TemplateResponse(
        request,
        "partials/timelapses/job_progress.html",
        {**context},
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
        request,
        "partials/timelapses/job_list.html",
        {**jobs_context},
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
    context = await view_service.cleanup_stale_jobs_and_build_context()
    return templates.TemplateResponse(
        request,
        "partials/timelapses/job_list.html",
        {**context},
    )
