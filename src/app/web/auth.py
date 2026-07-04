"""
Authentication for the web interface.

Supports two authentication modes:
1. Environment variables (WEB_USERNAME/WEB_PASSWORD) - legacy single-user mode
2. Database users - multi-user mode with hashed passwords

Authentication priority:
1. If WEB_PASSWORD is set, use env-based auth (existing behavior)
2. If env vars not set, check database users table
3. If neither exists, redirect to /setup for first-run wizard

Security features:
- Auto-detects HTTPS (via X-Forwarded-Proto or request scheme) for cookie security
- Rate limiting on login attempts to prevent brute force
- HTTPOnly cookies to prevent XSS token theft
- SameSite=Strict for CSRF protection
"""

import secrets
from collections import defaultdict
from datetime import datetime, timedelta
from time import time
from typing import cast

import config
from crud.user_crud import user_crud
from db.connection import async_session
from fastapi import Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from logging_config import get_logger
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Password hashing (argon2 - no length limits, more secure than bcrypt)
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

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

# Login rate limiting - track failed attempts per IP
# Structure: {ip: [(timestamp, ...], ...}
_login_attempts: dict[str, list[float]] = defaultdict(list)


def _get_client_ip(request: Request) -> str:
    """Get client IP, respecting X-Forwarded-For if trusted."""
    if config.WEB_TRUST_PROXY_HEADERS:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take the first IP (original client)
            return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_https_request(request: Request) -> bool:
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


def _should_set_secure_cookie(request: Request) -> bool:
    """
    Determine if cookies should have the 'secure' flag set.

    Modes:
    - "auto": Set secure=True only when HTTPS is detected (recommended for home users)
    - "always": Always set secure=True (requires HTTPS, will break on HTTP)
    - "never": Never set secure=True (for HTTP-only deployments)
    """
    mode = config.WEB_COOKIE_SECURE_MODE

    if mode == "always":
        return True
    elif mode == "never":
        return False
    else:  # "auto" (default)
        return _is_https_request(request)


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """
    Check if IP has exceeded login rate limit.

    Returns (is_allowed, seconds_until_allowed).
    Cleans up old attempts automatically.
    """
    now = time()
    window_seconds = config.WEB_LOGIN_RATE_WINDOW_SECONDS
    window_start = now - window_seconds

    # Clean up old attempts
    _login_attempts[ip] = [ts for ts in _login_attempts[ip] if ts > window_start]

    attempts = len(_login_attempts[ip])

    if attempts >= config.WEB_LOGIN_RATE_LIMIT:
        # Calculate when the oldest attempt will expire
        oldest = min(_login_attempts[ip]) if _login_attempts[ip] else now
        seconds_remaining = int(oldest + window_seconds - now) + 1
        return False, max(seconds_remaining, 1)

    return True, 0


def _record_login_attempt(ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    _login_attempts[ip].append(time())


def _clear_login_attempts(ip: str) -> None:
    """Clear login attempts on successful login."""
    if ip in _login_attempts:
        del _login_attempts[ip]


def uses_env_auth() -> bool:
    """Check if environment variable authentication is configured.

    Returns True if WEB_PASSWORD is set, meaning env-based auth should be used.
    """
    return bool(config.WEB_PASSWORD)


async def needs_setup(db: AsyncSession) -> bool:
    """Check if the first-run setup wizard is needed.

    Setup is needed when:
    - No WEB_PASSWORD environment variable is set, AND
    - No users exist in the database

    Returns True if the setup wizard should be shown.
    """
    # If env auth is configured, setup is not needed
    if uses_env_auth():
        return False

    # Check if any database users exist
    return not await user_crud.user_exists(db)


class AuthService:
    """Authentication service."""

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        return cast(bool, pwd_context.verify(plain_password, hashed_password))

    @staticmethod
    def get_password_hash(password: str) -> str:
        """Hash a password."""
        return cast(str, pwd_context.hash(password))

    @staticmethod
    def authenticate_user_env(username: str, password: str) -> bool:
        """Authenticate user credentials against environment variables."""
        return username == config.WEB_USERNAME and password == config.WEB_PASSWORD

    @staticmethod
    async def authenticate_user_db(db: AsyncSession, username: str, password: str) -> tuple[bool, int | None]:
        """Authenticate user credentials against database.

        Returns (success, user_id) tuple. user_id is set on successful auth.
        """
        user = await user_crud.authenticate(db, username, password)
        if user:
            return True, user.id
        return False, None

    @staticmethod
    async def authenticate_user(db: AsyncSession | None, username: str, password: str) -> tuple[bool, int | None]:
        """Authenticate user credentials.

        Checks env vars first for env username, then database for all users.
        Returns (success, user_id) tuple. user_id is None for env auth.
        """
        # If env auth is configured and username matches, try env auth first
        if uses_env_auth() and username == config.WEB_USERNAME:
            success = AuthService.authenticate_user_env(username, password)
            if success:
                return True, None

        # Try database auth for all users (including env user if env auth failed)
        if db is not None:
            return await AuthService.authenticate_user_db(db, username, password)

        return False, None

    @staticmethod
    def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
        """Create JWT access token."""
        to_encode = data.copy()

        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)

        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return cast(str, encoded_jwt)

    @staticmethod
    def verify_token(token: str) -> str | None:
        """Verify JWT token and return username."""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username: str = payload.get("sub")
            if username is None:
                return None
            return username
        except JWTError:
            return None


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
    # Check if setup is needed - redirect to /setup if so
    if not uses_env_auth():
        async with async_session() as db:
            if await needs_setup(db):
                return RedirectResponse(url="/setup", status_code=302)

    templates = request.app.state.templates
    context: dict = {"request": request}

    # Show success message if redirected from setup
    if request.query_params.get("setup_complete") == "1":
        context["success"] = "Account created successfully. Please sign in."

    return cast(Response, templates.TemplateResponse("pages/login.html", context))


async def login(request: Request, username: str = Form(...), password: str = Form(...)) -> Response:
    """Process login form with rate limiting and adaptive cookie security."""
    templates = request.app.state.templates
    client_ip = _get_client_ip(request)

    # Check rate limit before processing
    is_allowed, wait_seconds = _check_rate_limit(client_ip)
    if not is_allowed:
        logger.warning("Login rate limit exceeded", extra={"client_ip": client_ip})
        return cast(
            Response,
            templates.TemplateResponse(
                "pages/login.html",
                {
                    "request": request,
                    "error": f"Too many login attempts. Please wait {wait_seconds} seconds.",
                },
                status_code=429,
            ),
        )

    # Authenticate (supports both env and database auth)
    async with async_session() as db:
        success, user_id = await AuthService.authenticate_user(db, username, password)

        if not success:
            _record_login_attempt(client_ip)
            logger.warning("Failed login attempt", extra={"username": username, "client_ip": client_ip})
            return cast(
                Response,
                templates.TemplateResponse(
                    "pages/login.html",
                    {"request": request, "error": "Invalid username or password"},
                    status_code=400,
                ),
            )

        # Update last login time
        if user_id is not None:
            # Database user - update last login
            await user_crud.update_last_login(db, user_id)
        else:
            # Env user - sync to database and update last login
            env_user = await user_crud.sync_env_user(db, username)
            await user_crud.update_last_login(db, env_user.id)

        await db.commit()

    # Success - clear rate limit tracking
    _clear_login_attempts(client_ip)
    logger.info("Successful login", extra={"username": username, "client_ip": client_ip})

    # Create access token
    access_token_expires = timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = AuthService.create_access_token(data={"sub": username}, expires_delta=access_token_expires)

    # Determine cookie security based on request context
    use_secure_cookie = _should_set_secure_cookie(request)

    # Redirect to cameras with cookie
    response = RedirectResponse(url="/cameras", status_code=302)
    response.set_cookie(
        key=COOKIE_NAME,
        value=access_token,
        max_age=config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=use_secure_cookie,
        samesite="strict",
    )

    return response


async def logout(request: Request) -> Response:
    """Logout user."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response
