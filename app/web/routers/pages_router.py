"""Main pages router for full page renders."""

from typing import cast

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.web.auth import login, login_form, logout
from app.web.deps import DbSession
from app.web.rate_limit import LOGIN_RATE, limiter

router = APIRouter(tags=["pages"])


@router.get("/", name="pages.root_redirect", response_class=RedirectResponse)
async def root_redirect(request: Request) -> Response:
    """Redirect root to cameras page."""
    return RedirectResponse(
        url=request.app.url_path_for("cameras.cameras_page"), status_code=302
    )


@router.get(
    "/dashboard", name="pages.dashboard_redirect", response_class=RedirectResponse
)
async def dashboard_redirect(request: Request) -> Response:
    """Redirect old dashboard URL to cameras page."""
    return RedirectResponse(
        url=request.app.url_path_for("cameras.cameras_page"), status_code=302
    )


@router.get("/login", name="pages.login_page", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    """Login page."""
    return await login_form(request)


@router.post("/login", name="pages.login_submit")
@limiter.limit(LOGIN_RATE)
async def login_submit(request: Request, db: DbSession) -> Response:
    """Process login."""
    form = await request.form()
    username = cast(str, form.get("username", ""))
    password = cast(str, form.get("password", ""))
    return await login(request, db, username, password)


@router.get("/logout", name="pages.logout_page")
async def logout_page(request: Request) -> Response:
    """Logout."""
    return await logout(request)
