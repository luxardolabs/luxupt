"""Setup router for first-run user creation."""

from typing import Annotated, cast

from db.connection import DbSession
from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from logging_config import get_logger

from web.auth import needs_setup
from web.deps import TemplatesDep, UsersViewDep

logger = get_logger(__name__)

router = APIRouter(tags=["setup"])


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(
    request: Request,
    templates: TemplatesDep,
    db: DbSession,
) -> Response:
    """Display the first-run setup form.

    Redirects to login if setup is already complete.
    """
    if not await needs_setup(db):
        return RedirectResponse(url="/login", status_code=302)

    return cast(
        Response,
        templates.TemplateResponse(
            request,
            "pages/setup.html",
            {},
        ),
    )


@router.post("/setup", response_class=HTMLResponse)
async def create_first_user(
    request: Request,
    templates: TemplatesDep,
    view_service: UsersViewDep,
    db: DbSession,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
) -> Response:
    """Create the first admin user.

    Validates form input and creates the user if setup is still needed.
    """
    # Race condition protection - check again before creating
    if not await needs_setup(db):
        return RedirectResponse(url="/login", status_code=302)

    errors = await view_service.validate_user_create(
        username, password, confirm_password
    )
    if errors:
        return cast(
            Response,
            templates.TemplateResponse(
                request,
                "pages/setup.html",
                {"errors": errors, "username": username.strip()},
                status_code=400,
            ),
        )

    success, _message, created_username = await view_service.create_user(
        username, password, is_admin=True
    )
    if not success or created_username is None:
        return cast(
            Response,
            templates.TemplateResponse(
                request,
                "pages/setup.html",
                {
                    "errors": ["Failed to create user. Please try again."],
                    "username": username,
                },
                status_code=500,
            ),
        )

    logger.info("First admin user created", extra={"username": created_username})

    # Redirect to login with success message
    return RedirectResponse(url="/login?setup_complete=1", status_code=302)
