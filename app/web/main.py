"""
FastAPI web interface for LuxUPT.

Provides a clean, modern interface for monitoring and managing
the timelapse system without impacting core functionality.
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import StrictUndefined

from app import config
from app.camera_manager import CameraManager, CameraManagerSettings
from app.crud import activity_crud, camera_crud
from app.crud.fetch_settings_crud import fetch_settings_crud
from app.db.connection import close_db, get_db, get_db_context, init_db
from app.logging_config import get_logger, setup_logging
from app.models.enum_model import ActivityType
from app.services.core.health_core_service import HealthCoreService, HealthStatus
from app.services.core.metrics_core_service import MetricsCoreService
from app.utils.exception_handlers import general_exception_handler

from .auth import get_current_user
from .middleware import (
    AuthRedirectMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from .template_helpers import paginated_url

# Module logger
logger = get_logger(__name__)

# Resolved once at import, not inside the async server entrypoint: os.path.abspath touches
# the filesystem (getcwd), and a blocking path call in an async def stalls the event loop
# (ruff ASYNC240). The value is a static package path, so import time is the right home.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _create_monitored_task(coro: Any, name: str) -> asyncio.Task[Any]:
    """Create an asyncio task with exception monitoring.

    Adds a done callback that logs any unhandled exceptions from background tasks,
    preventing the 'Task exception was never retrieved' warning.
    """

    def _handle_task_exception(task: asyncio.Task[Any]) -> None:
        """Log unhandled exceptions from background tasks to prevent silent failures."""
        if task.cancelled():
            logger.debug("Background task was cancelled", extra={"task": name})
            return
        exc = task.exception()
        if exc:
            logger.error(
                "Background task '%s' failed with exception",
                name,
                extra={"task_name": name, "error": str(exc)},
                exc_info=exc,
            )

    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_handle_task_exception)
    return task


async def log_database_settings() -> None:
    """Log consolidated database settings after initialization."""
    from app.crud.scheduler_settings_crud import (  # noqa: PLC0415
        scheduler_settings_crud,
    )

    async for db in get_db():
        try:
            # Get fetch settings
            fetch_settings = await fetch_settings_crud.get_settings(db)
            intervals = fetch_settings.get_intervals()

            # Get scheduler settings
            scheduler_settings = await scheduler_settings_crud.get_settings(db)

            # Get active cameras count
            active_cameras = await camera_crud.get_active(db)
            active_count = len(active_cameras) if active_cameras else 0

            logger.info(
                "Database settings loaded",
                extra={
                    "fetch_intervals": intervals,
                    "scheduler_enabled": scheduler_settings.enabled,
                    "scheduler_run_time": scheduler_settings.run_time,
                    "scheduler_days_ago": scheduler_settings.days_ago,
                    "scheduler_concurrent_jobs": scheduler_settings.concurrent_jobs,
                    "active_cameras": active_count,
                },
            )
        except Exception as e:
            logger.warning(
                "Could not load database settings for logging", extra={"error": str(e)}
            )
        break


async def sync_cameras_to_db(camera_manager: CameraManager) -> None:
    """Sync cameras from UniFi Protect API to database and run capability detection for new cameras."""
    from datetime import datetime as dt  # noqa: PLC0415 (lazy alias, sync loop)

    try:
        logger.info("Syncing cameras to database")
        cameras = await camera_manager.get_cameras(force_refresh=True)

        new_cameras = []
        new_camera_objects = []  # Track API camera objects for detection

        async for db in get_db():
            for camera in cameras:
                # Check if camera already exists
                existing = await camera_crud.get_by_camera_id(db, camera.id)

                # Build base camera data (always updated)
                camera_data = {
                    "camera_id": camera.id,
                    "name": camera.name,
                    "safe_name": camera.safe_name,
                    "mac": camera.mac,
                    "model_key": camera.model_key,
                    "video_mode": camera.video_mode,
                    "hdr_type": camera.hdr_type,
                    "state": camera.state,
                    "is_connected": camera.is_connected,
                    "is_recording": camera.is_recording,
                    "supports_full_hd_snapshot": camera.supports_full_hd_snapshot,
                    "has_hdr": camera.has_hdr,
                    "has_mic": camera.has_mic,
                    "has_speaker": camera.has_speaker,
                    "smart_detect_types": camera.smart_detect_types,
                }

                # Set defaults for new cameras only
                if not existing:
                    camera_data["first_discovered_at"] = dt.now(UTC)
                    camera_data["is_active"] = True
                    camera_data["capture_method"] = "auto"
                    camera_data["rtsp_quality"] = "high"
                    camera_data["enabled_intervals"] = [60]  # Default to 60s only
                    new_cameras.append(camera.name)
                    if camera.is_connected:
                        new_camera_objects.append(camera)

                await camera_crud.upsert_from_dict(db, data=camera_data)

            await db.commit()
            logger.info(
                "Synced cameras to database", extra={"camera_count": len(cameras)}
            )

            if new_cameras:
                logger.info("New cameras discovered", extra={"cameras": new_cameras})

            # Run capability detection for new connected cameras
            if new_camera_objects:
                logger.info(
                    "Running capability detection",
                    extra={"camera_count": len(new_camera_objects)},
                )

                for camera in new_camera_objects:
                    try:
                        capabilities = await camera_manager.detect_camera_capabilities(
                            camera
                        )

                        # Update camera with detection results
                        await camera_crud.update_capability_detection(
                            db,
                            camera.id,
                            api_max_resolution=capabilities.get("api_max_resolution"),
                            rtsp_max_resolution=capabilities.get("rtsp_max_resolution"),
                            recommended_method=capabilities.get("recommended_method"),
                        )

                        logger.info(
                            "Detected camera capabilities",
                            extra={
                                "camera": camera.name,
                                "api_resolution": capabilities.get(
                                    "api_max_resolution"
                                ),
                                "rtsp_resolution": capabilities.get(
                                    "rtsp_max_resolution"
                                ),
                                "recommended_method": capabilities.get(
                                    "recommended_method"
                                ),
                            },
                        )

                    except Exception as e:
                        logger.warning(
                            "Failed to detect capabilities",
                            extra={"camera": camera.name, "error": str(e)},
                        )

                await db.commit()
                logger.info("Capability detection complete for new cameras")

    except Exception as e:
        logger.exception("Failed to sync cameras to database", extra={"error": str(e)})


# Configure logging immediately at module load
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Manage application lifespan."""
    from app.fetch_service import (  # noqa: PLC0415
        FetchService,
    )
    from app.services.core.backup_core_service import (  # noqa: PLC0415
        BackupCoreService,
    )
    from app.services.core.user_core_service import (  # noqa: PLC0415
        UserCoreService,
    )
    from app.timelapse_service import (  # noqa: PLC0415
        TimelapseService,
    )

    # Startup
    logger.info("Starting web interface")

    # Initialize database
    logger.info("Initializing database")
    await init_db()

    # Sync the env-managed user ONCE at startup. It used to happen on every users-page
    # render, which made a GET write to the database -- CSRF-reachable under SameSite=Lax
    # (fw.state_changing_get). The env credentials only change on restart, so startup is
    # where this belongs; the page render is now a pure read.
    async with get_db_context() as session:
        await UserCoreService(session).sync_env_user()

    # Load CameraManager settings from database
    async with get_db_context() as session:
        fetch_settings = await fetch_settings_crud.get_settings(session)
        cm_settings = CameraManagerSettings(
            base_url=config.UNIFI_PROTECT_BASE_URL or fetch_settings.base_url or "",
            api_key=config.UNIFI_PROTECT_API_KEY or fetch_settings.api_key or "",
            verify_ssl=config.UNIFI_PROTECT_VERIFY_SSL
            if config.UNIFI_PROTECT_BASE_URL
            else fetch_settings.verify_ssl,
            request_timeout=fetch_settings.request_timeout,
            rate_limit=fetch_settings.rate_limit,
            rate_limit_buffer=fetch_settings.rate_limit_buffer,
            min_offset_seconds=fetch_settings.min_offset_seconds,
            max_offset_seconds=fetch_settings.max_offset_seconds,
            camera_refresh_interval=fetch_settings.camera_refresh_interval,
        )

    # Initialize camera manager
    camera_manager = CameraManager(cm_settings)
    await camera_manager.__aenter__()

    # Store in app state
    app.state.camera_manager = camera_manager
    app.state.start_time = datetime.now(UTC)

    # Sync cameras from API to database
    await sync_cameras_to_db(camera_manager)

    # Log consolidated database settings
    await log_database_settings()

    # Start background services (they check database settings for enabled status)
    tasks = []

    logger.info("Starting fetch service")
    fetch_service = FetchService()
    tasks.append(_create_monitored_task(fetch_service.start(), "fetch_service"))

    logger.info("Starting timelapse service")
    timelapse_service = TimelapseService()
    tasks.append(_create_monitored_task(timelapse_service.start(), "timelapse_service"))

    logger.info("Starting backup service")
    backup_service = BackupCoreService()
    tasks.append(_create_monitored_task(backup_service.start(), "backup_service"))

    # Record service start in the activity log
    async with get_db_context() as session:
        await activity_crud.log(
            session,
            activity_type=ActivityType.SERVICE_STARTED,
            message="LuxUPT service started",
        )
        await session.commit()

    yield

    # Shutdown
    logger.info("Shutting down services")

    # Record service stop in the activity log
    try:
        async with get_db_context() as session:
            await activity_crud.log(
                session,
                activity_type=ActivityType.SERVICE_STOPPED,
                message="LuxUPT service stopped",
            )
            await session.commit()
    except Exception as e:
        logger.warning("Failed to log service stop", extra={"error": str(e)})

    # Stop background services
    await fetch_service.stop()
    await timelapse_service.stop()
    await backup_service.stop()

    # Cancel any remaining tasks
    for task in tasks:
        if not task.done():
            task.cancel()

    # Cleanup camera manager
    await camera_manager.__aexit__(None, None, None)

    # Close database connections
    await close_db()


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""

    app = FastAPI(
        title="LuxUPT",
        description="Web interface for monitoring and managing time-lapse operations",
        version=os.getenv("BUILD_VERSION", "dev"),
        lifespan=lifespan,
        docs_url="/docs" if config.LOGGING_LEVEL == "DEBUG" else None,
        redoc_url="/redoc" if config.LOGGING_LEVEL == "DEBUG" else None,
    )

    # Exception handlers — return HTML error pages, not JSON
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
        """Handle all HTTPExceptions with HTML error pages."""
        templates_inst: Jinja2Templates | None = getattr(
            request.app.state, "templates", None
        )
        if templates_inst is None:
            # Last resort: the template engine itself is unavailable, so a template cannot
            # be the answer here. Plain text keeps markup out of Python entirely
            # (fw.no_inline_html) and is perfectly adequate for an already-degraded state.
            return PlainTextResponse(
                f"{exc.status_code} {exc.detail}",
                status_code=exc.status_code,
            )

        error_titles = {
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            500: "Server Error",
            503: "Service Unavailable",
        }

        # Use dedicated error page if available, fall back to generic
        dedicated_pages = {401, 403, 404, 500, 503}
        template_name = (
            f"pages/{exc.status_code}.html"
            if exc.status_code in dedicated_pages
            else "pages/error.html"
        )
        return templates_inst.TemplateResponse(
            request,
            template_name,
            {
                "status_code": exc.status_code,
                "title": error_titles.get(exc.status_code, f"Error {exc.status_code}"),
                "message": exc.detail,
            },
            status_code=exc.status_code,
        )

    # ONE content-negotiating handler for every UNEXPECTED exception (luxarch --emit
    # exception-handler). Routes no longer wrap their bodies in `except Exception` to render
    # an error partial: that reported bugs to the user as a 200 and to monitoring as success,
    # and it made filterwarnings=error inert on the route (fw.route_no_broad_except).
    # Registered on Exception, not on 500: a raised exception never reaches a status-code
    # handler, so the old @app.exception_handler(500) only ever fired for an explicit 500.
    app.add_exception_handler(Exception, general_exception_handler)

    # Add middleware (order matters - first added = outermost = runs first on request, last on response)
    # CORS must be outermost to handle preflight requests
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.WEB_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AuthRedirectMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    # Static files
    # Static assets live at the fleet-canonical app/static/ (app-wide, a sibling of web/),
    # served at the /static URL. __file__ is app/web/main.py, so go up to app/.
    static_path = Path(__file__).parent.parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    # Templates live at the fleet-canonical app/templates/web/ (app-wide, a sibling of static/;
    # email/llm would be peer subdirs). __file__ is app/web/main.py, so go up to app/.
    templates_path = Path(__file__).parent.parent / "templates" / "web"
    # StrictUndefined: a missing/renamed/typo'd template variable RAISES instead of rendering
    # an empty string. Jinja's default silently renders "" -- a dropped view->template value or
    # a stale context key then looks fine and busts nothing (fw.jinja_strict_undefined).
    templates = Jinja2Templates(directory=str(templates_path))
    templates.env.undefined = StrictUndefined

    # Register custom template filters
    from .template_filters import register_filters  # noqa: PLC0415 (lazy, app-factory)

    register_filters(templates)

    # partials/nav.html reads `user` on every page that extends the base layout, so it is
    # a GLOBAL, not a per-route key. Declaring the default here clears it everywhere at
    # once; a route with a real user overrides it via its own context.
    templates.env.globals["user"] = None

    # Add template globals
    templates.env.globals.update(
        {
            "config": config,
            "datetime": datetime,
            "paginated_url": paginated_url,
            # Cache-bust token for first-party static assets (fw.static_assets_cache_busted):
            # the OCI revision (short git SHA), stamped as BUILD_COMMIT in the image
            # (cache-busting playbook — one value shared with org.opencontainers.image.revision).
            "static_version": os.getenv("BUILD_COMMIT", "dev"),
            "len": len,
            "enumerate": enumerate,
            "range": range,
            "max": max,
            "min": min,
            "round": round,
            "int": int,
            "str": str,
            "float": float,
            "dev_mode": config.LOGGING_LEVEL == "DEBUG",
        }
    )

    app.state.templates = templates

    # Import and include HTMX routers (SQLite + view services architecture)
    from .routers import (  # noqa: PLC0415 (lazy router import avoids the app-factory import cycle)
        cameras_router,
        images_router,
        pages_router,
        setup_router,
        system_router,
        timelapse_jobs_router,
        timelapse_scheduler_router,
        timelapses_router,
    )

    # Pages router handles login, logout, dashboard
    app.include_router(pages_router, tags=["pages"])
    # Setup router handles first-run user creation
    app.include_router(setup_router, tags=["setup"])
    # Feature routers
    app.include_router(cameras_router, prefix="/cameras", tags=["cameras"])
    app.include_router(images_router, prefix="/images", tags=["images"])
    # Jobs + scheduler routers first: their specific paths register before the artifact
    # router's int-typed /{timelapse_id} catch-all (belt-and-suspenders; int typing already
    # prevents a collision).
    app.include_router(timelapse_jobs_router, prefix="/timelapses", tags=["timelapses"])
    app.include_router(
        timelapse_scheduler_router, prefix="/timelapses", tags=["timelapses"]
    )
    app.include_router(timelapses_router, prefix="/timelapses", tags=["timelapses"])
    app.include_router(system_router, prefix="/system", tags=["system"])

    # Root redirect
    @app.get("/", response_class=HTMLResponse)
    async def root(
        request: Request, user: str = Depends(get_current_user)
    ) -> RedirectResponse:
        """Redirect to cameras."""
        return RedirectResponse(url="/cameras", status_code=302)

    return app


def get_camera_manager(request: Request) -> CameraManager:
    """Get the camera manager from app state."""
    camera_manager: CameraManager | None = getattr(
        request.app.state, "camera_manager", None
    )
    if camera_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Camera manager not initialized",
        )
    return camera_manager


def get_start_time(request: Request) -> datetime:
    """Get the application start time from app state."""
    return getattr(request.app.state, "start_time", datetime.now(UTC))


# Create app instance
app = create_app()


# Health check endpoints
@app.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """Comprehensive health check endpoint."""
    camera_manager = getattr(request.app.state, "camera_manager", None)
    health_service = HealthCoreService(camera_manager=camera_manager)

    async for db in get_db():
        health_status = await health_service.get_health_status(db)
        break

    # Return appropriate HTTP status code
    if health_status["status"] == HealthStatus.UNHEALTHY:
        return JSONResponse(content=health_status, status_code=503)
    elif health_status["status"] == HealthStatus.DEGRADED:
        return JSONResponse(content=health_status, status_code=200)
    else:
        return JSONResponse(content=health_status, status_code=200)


@app.get("/health/live")
async def liveness_check() -> dict[str, str]:
    """Kubernetes liveness probe endpoint."""
    health_service = HealthCoreService()
    return await health_service.get_liveness()


@app.get("/health/ready")
async def readiness_check(request: Request) -> JSONResponse:
    """Kubernetes readiness probe endpoint."""
    camera_manager = getattr(request.app.state, "camera_manager", None)
    health_service = HealthCoreService(camera_manager=camera_manager)

    async for db in get_db():
        readiness = await health_service.get_readiness(db)
        break

    if readiness["status"] == HealthStatus.UNHEALTHY:
        return JSONResponse(content=readiness, status_code=503)
    return JSONResponse(content=readiness, status_code=200)


@app.get("/metrics")
async def prometheus_metrics(request: Request) -> PlainTextResponse:
    """Prometheus metrics endpoint."""
    start_time = getattr(request.app.state, "start_time", datetime.now(UTC))
    metrics_service = MetricsCoreService(start_time=start_time)

    async for db in get_db():
        metrics = await metrics_service.get_all_metrics(db)
        break

    return PlainTextResponse(
        content=metrics, media_type="text/plain; version=0.0.4; charset=utf-8"
    )


async def start_web_server() -> None:
    """Start the web server.

    The server can start in three modes:
    1. Environment auth: WEB_PASSWORD is set, uses env-based authentication
    2. Database auth: Users exist in database, uses database authentication
    3. Setup mode: Neither configured, redirects to /setup for first user creation
    """
    # Server will start regardless - auth is handled by middleware
    # The setup wizard will be shown if no auth is configured
    logger.info(
        "Starting web server",
        extra={"port": config.WEB_PORT, "dev_reload": config.WEB_DEV_RELOAD},
    )

    # Build uvicorn config
    uvicorn_kwargs: dict[str, Any] = {
        "host": "0.0.0.0",
        "port": config.WEB_PORT,
        "log_level": "info" if config.LOGGING_LEVEL == "DEBUG" else "warning",
        "access_log": config.LOGGING_LEVEL == "DEBUG",
    }

    if config.WEB_DEV_RELOAD:
        # Use string import for reload mode
        uvicorn_kwargs["app"] = "web.main:app"
        uvicorn_kwargs["reload"] = True
        # Watch the entire app directory for changes - use absolute path

        app_dir = _APP_DIR
        uvicorn_kwargs["reload_dirs"] = [app_dir]
        # Include templates and static files
        uvicorn_kwargs["reload_includes"] = ["*.py", "*.html", "*.css", "*.js"]
        logger.info(
            "Hot reload watching", extra={"dirs": uvicorn_kwargs["reload_dirs"]}
        )
    else:
        uvicorn_kwargs["app"] = app

    server_config = uvicorn.Config(**uvicorn_kwargs)
    server = uvicorn.Server(server_config)
    await server.serve()
