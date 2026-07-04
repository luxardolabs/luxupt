"""Setup router for first-run user creation."""

from typing import Annotated, cast

from crud.user_crud import user_crud
from db.connection import DbSession
from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from logging_config import get_logger

from web.auth import needs_setup
from web.deps import TemplatesDep

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
            "pages/setup.html",
            {"request": request},
        ),
    )


@router.post("/setup", response_class=HTMLResponse)
async def create_first_user(
    request: Request,
    templates: TemplatesDep,
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

    # Validate form input
    errors = []

    # Username validation
    username = username.strip()
    if not username:
        errors.append("Username is required")
    elif len(username) > 64:
        errors.append("Username must be 64 characters or less")

    # Password validation
    if not password:
        errors.append("Password is required")
    elif password != confirm_password:
        errors.append("Passwords do not match")

    if errors:
        return cast(
            Response,
            templates.TemplateResponse(
                "pages/setup.html",
                {"request": request, "errors": errors, "username": username},
                status_code=400,
            ),
        )

    # Create the first admin user
    try:
        user = await user_crud.create_user(db, username=username, password=password, is_admin=True)
        await db.commit()
        logger.info("First admin user created", extra={"username": user.username})

        # Redirect to login with success message
        return RedirectResponse(url="/login?setup_complete=1", status_code=302)

    except Exception as e:
        logger.error("Failed to create first user", extra={"error": str(e)})
        return cast(
            Response,
            templates.TemplateResponse(
                "pages/setup.html",
                {"request": request, "errors": ["Failed to create user. Please try again."], "username": username},
                status_code=500,
            ),
        )
