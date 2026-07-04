"""
Middleware for the web interface.

Contains security headers, request logging, and authentication middleware.
"""

import time

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from logging_config import get_logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .auth import (
    COOKIE_NAME,
    AuthService,
    _is_https_request,
    needs_setup,
    uses_env_auth,
)

logger = get_logger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all responses.

    These headers work regardless of HTTP/HTTPS and provide defense-in-depth:
    - X-Frame-Options: Prevent clickjacking
    - X-Content-Type-Options: Prevent MIME sniffing
    - X-XSS-Protection: Legacy XSS protection
    - Referrer-Policy: Control referrer information
    - Content-Security-Policy: Restrict resource loading
    - Strict-Transport-Security: HTTPS enforcement (only when accessed via HTTPS)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Add security headers to every response."""
        response = await call_next(request)

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # XSS protection (legacy but still useful for older browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Content Security Policy - restrict resource loading
        # Note: 'unsafe-eval' required for Alpine.js dynamic expressions
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )

        # Add HSTS header only when accessed via HTTPS
        # This tells browsers to always use HTTPS in the future
        if _is_https_request(request):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log all HTTP requests with timing information.

    Skips logging for static files unless there's an error.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Log request method, URL, status code, and timing. Skip static files unless errored."""
        start_time = time.time()
        try:
            response = await call_next(request)
            process_time = time.time() - start_time

            # Only log non-static requests or errors
            if not request.url.path.startswith("/static") or response.status_code >= 400:
                logger.info(
                    "HTTP request completed",
                    extra={
                        "method": request.method,
                        "url": str(request.url),
                        "status_code": response.status_code,
                        "process_time_s": round(process_time, 3),
                    },
                )

            return response
        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                "HTTP request failed",
                extra={
                    "method": request.method,
                    "url": str(request.url),
                    "error": str(e),
                    "process_time_s": round(process_time, 3),
                },
                exc_info=True,
            )
            raise


class AuthRedirectMiddleware(BaseHTTPMiddleware):
    """
    Handle authentication redirects automatically.

    - Public paths (/login, /setup, /health, /static) are allowed without auth
    - If setup is needed (no env auth AND no DB users), redirect to /setup
    - API endpoints return 401 JSON response if not authenticated
    - Page requests redirect to login page if not authenticated
    """

    # Paths that don't require authentication
    PUBLIC_PATHS = {"/login", "/setup", "/health", "/metrics", "/static"}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Check authentication and redirect to login or setup if needed."""
        # Check if this is a public path or static file
        if any(request.url.path.startswith(path) for path in self.PUBLIC_PATHS):
            return await call_next(request)

        # Check authentication
        token = request.cookies.get(COOKIE_NAME)

        if not token:
            return await self._handle_unauthenticated(request)

        # Verify token
        username = AuthService.verify_token(token)
        if username is None:
            # Invalid token - redirect to login and clear cookie
            response = await self._create_auth_redirect(request)
            response.delete_cookie(COOKIE_NAME)
            return response

        # Token is valid, continue with request
        return await call_next(request)

    def _is_htmx_request(self, request: Request) -> bool:
        """Check if this is an HTMX request."""
        return request.headers.get("HX-Request") == "true"

    async def _needs_setup_check(self) -> bool:
        """Check if setup is needed (async database check)."""
        # If env auth is configured, setup is never needed
        if uses_env_auth():
            return False

        # Check database for users
        from db.connection import async_session

        async with async_session() as db:
            return await needs_setup(db)

    async def _create_auth_redirect(self, request: Request) -> Response:
        """Create appropriate redirect based on request type and auth state."""
        # Check if setup is needed
        setup_needed = await self._needs_setup_check()
        redirect_url = "/setup" if setup_needed else "/login"

        if self._is_htmx_request(request):
            # For HTMX requests, use HX-Redirect header for full page navigation
            response = Response(status_code=200)
            response.headers["HX-Redirect"] = redirect_url
            return response
        else:
            # For regular requests, use HTTP redirect
            return RedirectResponse(url=redirect_url, status_code=302)

    async def _handle_unauthenticated(self, request: Request) -> Response:
        """Handle unauthenticated requests based on request type."""
        if request.url.path.startswith("/api/"):
            # For API calls, return 401 JSON
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        else:
            # For page/HTMX requests, redirect to login or setup
            return await self._create_auth_redirect(request)
