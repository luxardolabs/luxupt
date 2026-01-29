"""Web routers for the application."""

from web.routers.cameras_router import router as cameras_router
from web.routers.images_router import router as images_router
from web.routers.pages_router import router as pages_router
from web.routers.setup_router import router as setup_router
from web.routers.system_router import router as system_router
from web.routers.timelapses_router import router as timelapses_router

__all__ = [
    "cameras_router",
    "images_router",
    "pages_router",
    "setup_router",
    "system_router",
    "timelapses_router",
]
