"""Main pages router for full page renders."""

from typing import cast

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from web.auth import login, login_form, logout

router = APIRouter(tags=["pages"])


@router.get("/", response_class=RedirectResponse)
async def root_redirect() -> Response:
    """Redirect root to cameras page."""
    return RedirectResponse(url="/cameras", status_code=302)


@router.get("/dashboard", response_class=RedirectResponse)
async def dashboard_redirect() -> Response:
    """Redirect old dashboard URL to cameras page."""
    return RedirectResponse(url="/cameras", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    """Login page."""
    return await login_form(request)


@router.post("/login")
async def login_submit(request: Request) -> Response:
    """Process login."""
    form = await request.form()
    username = cast(str, form.get("username", ""))
    password = cast(str, form.get("password", ""))
    return await login(request, username, password)


@router.get("/logout")
async def logout_page(request: Request) -> Response:
    """Logout."""
    return await logout(request)
