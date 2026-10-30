# luxarch:admin-bootstrap-router asset v4 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit admin-bootstrap-router`.
"""`/setup`: the first admin, claimed with the token from the server log.

`luxarch --emit admin-bootstrap-router`. Drop this in at `app/web/routers/admin_bootstrap_router.py`
and include `router` in the app. HTTP only: every decision is in the view service
(`luxarch --emit admin-bootstrap-view`). The standard is
`luxarch --doc FLEET-ADMIN-BOOTSTRAP-STANDARD`.

Both routes are public by nature (nobody can be signed in when there are no users), so declare
`/setup` among your public routes. They share the route name `admin.setup`, which the view service
resolves wherever it sends a visitor to the form (`fw.urls_from_route_names`). The POST accepts a credential (the token), so it carries a rate
limit (`fw.credential_routes_limited`; `--doc FLEET-RATE-LIMIT-STANDARD`). On Python 3.14+ the
slowapi decorator needs that standard's §4 carve-out.

`SETUP_TEMPLATE` is your page: extend your layout and
`{% include "partials/admin_setup_form.html" %}` (`luxarch --emit admin-bootstrap-form`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

# isort: split
# --- EDIT THIS: your limiter, request session and template environment (fleet homes shown).
from app.db.database import get_db
from app.services.views.admin_bootstrap_view_service import (
    AdminBootstrapViewService,
    SetupForm,
)
from app.web.rate_limit import limiter
from app.web.templates import templates

if TYPE_CHECKING:
    from slowapi import Limiter

# --- EDIT THIS: the page that includes partials/admin_setup_form.html.
SETUP_TEMPLATE = "pages/setup.html"

# Your limiter under slowapi's own type, so the decorator below is typed for strict mypy
# whatever your rate_limit module declares.
setup_limiter: Limiter = limiter

router = APIRouter(tags=["setup"])


@router.get(
    "/setup", name="admin.setup", response_class=HTMLResponse, include_in_schema=False
)
async def setup_page(
    request: Request, db: Annotated[AsyncSession, Depends(get_db, scope="function")]
) -> Response:
    context = await AdminBootstrapViewService().setup_page(db)
    if context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    page: Response = templates.TemplateResponse(request, SETUP_TEMPLATE, context)
    return page


@router.post(
    "/setup", name="admin.setup", response_class=HTMLResponse, include_in_schema=False
)
@setup_limiter.limit("5/minute")
async def submit_setup(
    request: Request,
    token: Annotated[str, Form()],
    login: Annotated[str, Form()],
    password: Annotated[str, Form()],
    password_confirm: Annotated[str, Form()],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> Response:
    outcome = await AdminBootstrapViewService().submit_setup(
        request, db, SetupForm(token, login, password, password_confirm)
    )
    if outcome.closed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if outcome.redirect:
        return RedirectResponse(outcome.redirect, status_code=status.HTTP_303_SEE_OTHER)
    page: Response = templates.TemplateResponse(
        request, SETUP_TEMPLATE, outcome.context, status_code=outcome.status_code
    )
    return page
