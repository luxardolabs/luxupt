"""
Authentication for the web interface.

Users sign in against the users table, the only credential store. The first admin comes from
INITIAL_ADMIN_* or /setup, and a locked-out owner recovers with
``python -m app.cli reset-password <username>`` (luxarch --doc FLEET-ADMIN-BOOTSTRAP-STANDARD).
A user on a seeded, reset or admin-assigned password is sent to the change form at sign-in.

Security features:
- Auto-detects HTTPS (via X-Forwarded-Proto or request scheme) for cookie security
- Rate limiting on login attempts to prevent brute force
- HTTPOnly cookies to prevent XSS token theft
- SameSite=Strict for CSRF protection
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import jwt
from fastapi import Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.crud.user_crud import user_crud
from app.db.database import get_db_context
from app.models.user_model import User
from app.services.views.admin_bootstrap_view_service import AdminBootstrapViewService
from app.utils.password_hash import hash_password, verify_password
from app.web.rate_limit import client_ip

logger = logging.getLogger(__name__)

# Password hashing lives in one home — app/utils/password_hash.py (argon2, no length limit).

# JWT settings - use persistent secret from config, or generate one
if config.WEB_SESSION_SECRET:
    SECRET_KEY = config.WEB_SESSION_SECRET
else:
    SECRET_KEY = secrets.token_urlsafe(32)
    logger.info(
        "Using auto-generated session secret (sessions reset on container restart)",
        extra={"tip": "Set WEB_SESSION_SECRET for persistent sessions"},
    )
ALGORITHM = "HS256"
COOKIE_NAME = f"access_token_{config.WEB_PORT}"


def is_https_request(request: Request) -> bool:
    """
    Detect if the request came over HTTPS.

    Checks X-Forwarded-Proto header (from reverse proxy) or request URL scheme.
    This allows proper cookie security when behind nginx/traefik with SSL termination.
    """
    if config.WEB_TRUST_PROXY_HEADERS:
        # Check reverse proxy header first
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "").lower()
        if forwarded_proto == "https":
            return True

    # Fall back to request URL scheme
    return request.url.scheme == "https"


async def needs_setup(request: Request, db: AsyncSession) -> bool:
    """True while there are no users: the login page and the auth redirect go to /setup."""
    return bool(await AdminBootstrapViewService().login_page_redirect(request, db))


class AuthService:
    """Authentication service."""

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        return verify_password(plain_password, hashed_password)

    @staticmethod
    def get_password_hash(password: str) -> str:
        """Hash a password."""
        return hash_password(password)

    @staticmethod
    async def authenticate_user_db(
        db: AsyncSession, username: str, password: str
    ) -> tuple[bool, int | None]:
        """Authenticate user credentials against database.

        Returns (success, user_id) tuple. user_id is set on successful auth.
        """
        user = await user_crud.authenticate(db, username, password)
        if user:
            return True, user.id
        return False, None

    @staticmethod
    async def authenticate_user(
        db: AsyncSession, username: str, password: str
    ) -> tuple[bool, int | None]:
        """Authenticate user credentials against the users table."""
        return await AuthService.authenticate_user_db(db, username, password)

    @staticmethod
    def create_access_token(
        data: dict[str, Any], expires_delta: timedelta | None = None
    ) -> str:
        """Create JWT access token."""
        to_encode = data.copy()

        if expires_delta:
            expire = datetime.now(UTC) + expires_delta
        else:
            expire = datetime.now(UTC) + timedelta(
                minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
            )

        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    @staticmethod
    def decode_session(token: str) -> tuple[str, int] | None:
        """(username, session_version) from a valid token, or None."""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        except jwt.PyJWTError:
            return None
        username = payload.get("sub")
        version = payload.get("sv")
        if not isinstance(username, str) or not isinstance(version, int):
            return None
        return username, version

    @staticmethod
    def verify_token(token: str) -> str | None:
        """Verify JWT token and return username."""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username: str | None = payload.get("sub")
            if username is None:
                return None
            return username
        except jwt.PyJWTError:
            return None


async def session_user(db: AsyncSession, token: str) -> User | None:
    """The user a session token still belongs to, or None.

    The signature alone is not enough: a token outlives a deleted or renamed user, a password
    reset and a password change unless it is checked against the row. Each of those makes this
    return None, so the session ends at its next request.
    """
    claims = AuthService.decode_session(token)
    if claims is None:
        return None
    username, version = claims
    user = await user_crud.get_by_username(db, username)
    if user is None or user.session_version != version:
        return None
    return user


def require_admin(request: Request) -> str:
    """The signed-in username, refusing a user who is not an admin (user management)."""
    username = get_current_user(request)
    if not getattr(request.state, "session_is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Administrators only"
        )
    return username


def get_current_user(request: Request) -> str:
    """Get current authenticated user from session."""
    token = request.cookies.get(COOKIE_NAME)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = AuthService.verify_token(token)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return username


async def login_form(request: Request) -> Response:
    """Display login form, or redirect to setup if no users exist."""
    # While there are no users there is nobody to sign in as: the login page is /setup.
    async with get_db_context() as db:
        setup_path = await AdminBootstrapViewService().login_page_redirect(request, db)
    if setup_path:
        return RedirectResponse(url=setup_path, status_code=302)

    templates = request.app.state.templates
    # Declared, not omitted: the template reads both, so the route OWNS providing them.
    # Leaving them out rendered empty under Jinja's default Undefined and raises under
    # StrictUndefined -- the context is the contract (fw.jinja_strict_undefined).
    context: dict[str, Any] = {"success": None, "error": None}

    # Show success message if redirected from setup
    if request.query_params.get("setup_complete") == "1":
        context["success"] = "Account created successfully. Please sign in."
    elif request.query_params.get("password_changed") == "1":
        context["success"] = "Password changed. Please sign in with the new one."

    return cast(
        Response, templates.TemplateResponse(request, "pages/login.html", context)
    )


async def login(
    request: Request,
    db: AsyncSession,
    username: str = Form(...),
    password: str = Form(...),
) -> Response:
    """Process the login form (rate-limited at the route) and set the session cookie.

    Uses the caller's request-scoped session; get_db() owns the commit.
    """
    templates = request.app.state.templates
    # The attempt limit is enforced BEFORE this runs: POST /login carries @limiter.limit
    # (app/web/rate_limit.py), which refuses with a 429. This only logs the address.
    address = client_ip(request)

    success, user_id = await AuthService.authenticate_user(db, username, password)

    if not success or user_id is None:
        logger.warning(
            "Failed login attempt", extra={"username": username, "client_ip": address}
        )
        return cast(
            Response,
            templates.TemplateResponse(
                request,
                "pages/login.html",
                # login.html reads `success` and `error` unguarded (StrictUndefined); pass both.
                {"success": None, "error": "Invalid username or password"},
                status_code=400,
            ),
        )

    signed_in = await user_crud.update_last_login(db, user_id)
    if signed_in is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    logger.info("Successful login", extra={"username": username, "client_ip": address})

    # Create access token
    access_token_expires = timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = AuthService.create_access_token(
        data={"sub": signed_in.username, "sv": signed_in.session_version},
        expires_delta=access_token_expires,
    )

    # Redirect to cameras with the session cookie. Cookie flags are hard literals
    # (fw.secure_cookies): the app is always served over HTTPS behind nginx.
    # A seeded, reset or admin-assigned password goes to the change form before any other page.
    destination = await AdminBootstrapViewService().login_destination(
        request, db, username, request.app.url_path_for("cameras.cameras_page")
    )
    response = RedirectResponse(url=destination, status_code=302)
    response.set_cookie(
        key=COOKIE_NAME,
        value=access_token,
        max_age=config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=True,
        samesite="lax",
    )

    return response


async def logout(request: Request) -> Response:
    """Logout user."""
    response = RedirectResponse(
        url=request.app.url_path_for("pages.login_page"), status_code=302
    )
    response.delete_cookie(COOKIE_NAME)
    return response
