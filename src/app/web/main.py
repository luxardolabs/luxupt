"""
FastAPI web interface for LuxUPT.

Provides a clean, modern interface for monitoring and managing
the timelapse system without impacting core functionality.
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import config
import uvicorn
from camera_manager import CameraManager, CameraManagerSettings
from crud import camera_crud
from crud.fetch_settings_crud import fetch_settings_crud
from db.connection import async_session, close_db, get_db, init_db
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
from logging_config import get_logger, setup_logging
from services.health_service import HealthService, HealthStatus
from services.metrics_service import MetricsService

from .auth import get_current_user
from .middleware import (
    AuthRedirectMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)

# Module logger
logger = get_logger(__name__)


def _create_monitored_task(coro: Any, name: str) -> "asyncio.Task[Any]":
    """Create an asyncio task with exception monitoring.

    Adds a done callback that logs any unhandled exceptions from background tasks,
    preventing the 'Task exception was never retrieved' warning.
    """

    def _handle_task_exception(task: "asyncio.Task[Any]") -> None:
        """Log unhandled exceptions from background tasks to prevent silent failures."""
        if task.cancelled():
            logger.debug("Background task was cancelled", extra={"task": name})
            return
        exc = task.exception()
        if exc:
            logger.error(
                f"Background task '{name}' failed with exception",
                extra={"task_name": name, "error": str(exc)},
                exc_info=exc,
            )

    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_handle_task_exception)
    return task


async def log_database_settings() -> None:
    """Log consolidated database settings after initialization."""
    from crud.fetch_settings_crud import fetch_settings_crud
    from crud.scheduler_settings_crud import scheduler_settings_crud

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
            logger.warning("Could not load database settings for logging", extra={"error": str(e)})
        break


async def sync_cameras_to_db(camera_manager: CameraManager) -> None:
    """Sync cameras from UniFi Protect API to database and run capability detection for new cameras."""
    from datetime import datetime as dt

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
                    camera_data["first_discovered_at"] = dt.now()
                    camera_data["is_active"] = True
                    camera_data["capture_method"] = "auto"
                    camera_data["rtsp_quality"] = "high"
                    camera_data["enabled_intervals"] = [60]  # Default to 60s only
                    new_cameras.append(camera.name)
                    if camera.is_connected:
                        new_camera_objects.append(camera)

                await camera_crud.upsert_from_dict(db, data=camera_data)

            await db.commit()
            logger.info("Synced cameras to database", extra={"camera_count": len(cameras)})

            if new_cameras:
                logger.info("New cameras discovered", extra={"cameras": new_cameras})

            # Run capability detection for new connected cameras
            if new_camera_objects:
                logger.info("Running capability detection", extra={"camera_count": len(new_camera_objects)})

                for camera in new_camera_objects:
                    try:
                        capabilities = await camera_manager.detect_camera_capabilities(camera)

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
                                "api_resolution": capabilities.get("api_max_resolution"),
                                "rtsp_resolution": capabilities.get("rtsp_max_resolution"),
                                "recommended_method": capabilities.get("recommended_method"),
                            },
                        )

                    except Exception as e:
                        logger.warning("Failed to detect capabilities", extra={"camera": camera.name, "error": str(e)})

                await db.commit()
                logger.info("Capability detection complete for new cameras")

    except Exception as e:
        logger.error("Failed to sync cameras to database", extra={"error": str(e)})


# Configure logging immediately at module load
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifespan."""
    from fetch_service import FetchService
    from services.backup_service import BackupService
    from timelapse_service import TimelapseService

    # Startup
    logger.info("Starting web interface")

    # Initialize database
    logger.info("Initializing database")
    await init_db()

    # Load CameraManager settings from database
    async with async_session() as session:
        fetch_settings = await fetch_settings_crud.get_settings(session)
        cm_settings = CameraManagerSettings(
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

    # Initialize camera manager
    camera_manager = CameraManager(cm_settings)
    await camera_manager.__aenter__()

    # Store in app state
    app.state.camera_manager = camera_manager
    app.state.start_time = datetime.now()

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
    backup_service = BackupService()
    tasks.append(_create_monitored_task(backup_service.start(), "backup_service"))

    yield

    # Shutdown
    logger.info("Shutting down services")

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
        version=os.getenv("LUXUPT_VERSION", "dev"),
        lifespan=lifespan,
        docs_url="/docs" if config.LOGGING_LEVEL == "DEBUG" else None,
        redoc_url="/redoc" if config.LOGGING_LEVEL == "DEBUG" else None,
    )

    # Exception handlers — return HTML error pages, not JSON
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
        """Handle all HTTPExceptions with HTML error pages."""
        templates_inst: Jinja2Templates | None = getattr(request.app.state, "templates", None)
        if templates_inst is None:
            return HTMLResponse(content=f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)

        error_titles = {
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            500: "Server Error",
            503: "Service Unavailable",
        }

        # Use dedicated error page if available, fall back to generic
        dedicated_pages = {401, 403, 404, 500, 503}
        template_name = f"pages/{exc.status_code}.html" if exc.status_code in dedicated_pages else "pages/error.html"
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

    @app.exception_handler(500)
    async def internal_server_error_handler(request: Request, exc: Exception) -> Response:
        """Handle unhandled exceptions with HTML error page."""
        logger.error("Internal server error", extra={"url": str(request.url), "error": str(exc)}, exc_info=True)
        templates_inst: Jinja2Templates | None = getattr(request.app.state, "templates", None)
        if templates_inst is None:
            return HTMLResponse(content="<h1>500</h1><p>Internal server error</p>", status_code=500)

        return templates_inst.TemplateResponse(
            request,
            "pages/500.html",
            {
                "status_code": 500,
                "title": "Server Error",
                "message": "An unexpected error occurred.",
            },
            status_code=500,
        )

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
    static_path = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    # Templates
    templates_path = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(templates_path))

    # Register custom template filters
    from .template_filters import register_filters

    register_filters(templates)

    # Add template globals
    templates.env.globals.update(
        {
            "config": config,
            "datetime": datetime,
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
    from .routers import (
        cameras_router,
        images_router,
        pages_router,
        setup_router,
        system_router,
        timelapses_router,
    )

    # Pages router handles login, logout, dashboard
    app.include_router(pages_router, tags=["pages"])
    # Setup router handles first-run user creation
    app.include_router(setup_router, tags=["setup"])
    # Feature routers
    app.include_router(cameras_router, prefix="/cameras", tags=["cameras"])
    app.include_router(images_router, prefix="/images", tags=["images"])
    app.include_router(timelapses_router, prefix="/timelapses", tags=["timelapses"])
    app.include_router(system_router, prefix="/system", tags=["system"])

    # Root redirect
    @app.get("/", response_class=HTMLResponse)
    async def root(request: Request, user: str = Depends(get_current_user)) -> RedirectResponse:
        """Redirect to cameras."""
        return RedirectResponse(url="/cameras", status_code=302)

    return app


def get_camera_manager(request: Request) -> CameraManager:
    """Get the camera manager from app state."""
    camera_manager: CameraManager | None = getattr(request.app.state, "camera_manager", None)
    if camera_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Camera manager not initialized",
        )
    return camera_manager


def get_start_time(request: Request) -> datetime:
    """Get the application start time from app state."""
    return getattr(request.app.state, "start_time", datetime.now())


# Create app instance
app = create_app()


# Health check endpoints
@app.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """Comprehensive health check endpoint."""
    camera_manager = getattr(request.app.state, "camera_manager", None)
    health_service = HealthService(camera_manager=camera_manager)

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
    health_service = HealthService()
    return await health_service.get_liveness()


@app.get("/health/ready")
async def readiness_check(request: Request) -> JSONResponse:
    """Kubernetes readiness probe endpoint."""
    camera_manager = getattr(request.app.state, "camera_manager", None)
    health_service = HealthService(camera_manager=camera_manager)

    async for db in get_db():
        readiness = await health_service.get_readiness(db)
        break

    if readiness["status"] == HealthStatus.UNHEALTHY:
        return JSONResponse(content=readiness, status_code=503)
    return JSONResponse(content=readiness, status_code=200)


@app.get("/metrics")
async def prometheus_metrics(request: Request) -> PlainTextResponse:
    """Prometheus metrics endpoint."""
    start_time = getattr(request.app.state, "start_time", datetime.now())
    metrics_service = MetricsService(start_time=start_time)

    async for db in get_db():
        metrics = await metrics_service.get_all_metrics(db)
        break

    return PlainTextResponse(content=metrics, media_type="text/plain; version=0.0.4; charset=utf-8")


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
    uvicorn_kwargs: dict = {
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
        import os

        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        uvicorn_kwargs["reload_dirs"] = [app_dir]
        # Include templates and static files
        uvicorn_kwargs["reload_includes"] = ["*.py", "*.html", "*.css", "*.js"]
        logger.info("Hot reload watching", extra={"dirs": uvicorn_kwargs["reload_dirs"]})
    else:
        uvicorn_kwargs["app"] = app

    server_config = uvicorn.Config(**uvicorn_kwargs)
    server = uvicorn.Server(server_config)
    await server.serve()
