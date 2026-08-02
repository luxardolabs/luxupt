"""Web routers for the application."""

from app.web.routers.cameras_router import router as cameras_router
from app.web.routers.images_router import router as images_router
from app.web.routers.pages_router import router as pages_router
from app.web.routers.setup_router import router as setup_router
from app.web.routers.system_router import router as system_router
from app.web.routers.timelapses_router import router as timelapses_router

__all__ = [
    "cameras_router",
    "images_router",
    "pages_router",
    "setup_router",
    "system_router",
    "timelapses_router",
]
