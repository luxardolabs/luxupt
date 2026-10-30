"""The signed-in user's own account: /account/password."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.views.account_view_service import AccountViewService
from app.web.auth import get_current_user
from app.web.deps import DbSession, TemplatesDep
from app.web.rate_limit import LOGIN_RATE, limiter

router = APIRouter(tags=["account"])

TEMPLATE = "pages/account_password.html"


@router.get(
    "/account/password",
    name="account.change_password_page",
    response_class=HTMLResponse,
)
async def change_password_page(
    request: Request,
    templates: TemplatesDep,
    db: DbSession,
    user: Annotated[str, Depends(get_current_user)],
) -> Response:
    """The change-password form; a seeded or reset password lands here at sign-in."""
    context = await AccountViewService().change_password_context(db, user)
    return templates.TemplateResponse(request, TEMPLATE, context)


@router.post(
    "/account/password",
    name="account.change_password_submit",
    response_class=HTMLResponse,
)
@limiter.limit(LOGIN_RATE)
async def change_password_submit(
    request: Request,
    templates: TemplatesDep,
    db: DbSession,
    user: Annotated[str, Depends(get_current_user)],
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
) -> Response:
    """Change the signed-in user's password (rate-limited: it verifies a credential)."""
    outcome = await AccountViewService().submit_change(
        db, user, current_password, new_password, confirm_password
    )
    if outcome.redirect:
        return RedirectResponse(outcome.redirect, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, TEMPLATE, outcome.context, status_code=outcome.status_code
    )
