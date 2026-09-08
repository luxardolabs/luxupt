"""Canonical fleet exception handler — ONE content-negotiating handler for UNEXPECTED errors.

Emitted by ``luxarch --emit exception-handler``. Wire it in your app factory:

    from app.utils.exception_handlers import general_exception_handler
    app.add_exception_handler(Exception, general_exception_handler)

The fleet ruling (WWWLUXARDO-116, ``luxarch --playbook htmx-error-ux``): a route handler NEVER
wraps its body in a broad ``except Exception`` to render an error partial. That idiom (a) reports a
bug to the user as a normal 200 and to monitoring as a success, and (b) makes ``filterwarnings =
error`` INERT on that route — a raised DeprecationWarning is caught by the route's own ``except`` and
returned 200, so the route-smoke early-warning system silently does nothing on exactly the routes it
exists for. ``fw.route_no_broad_except`` reds that shape.

Instead an unexpected exception PROPAGATES to this single handler, which decides the body by request
type, always at status 500:

  * an ``/api*`` path        -> JSON (a machine client)
  * an ``HX-Request`` header -> an HTML error PARTIAL + ``HX-Error-Swap: true`` (the client swaps it)
  * otherwise                -> a full HTML error page

Pair it with the client swap: ``luxarch --emit htmx-error-swap`` (HTMX 2 does not swap non-2xx by
default, so without it an HX request would leave the page unchanged). Adapt the template accessor
(``request.app.state.templates`` here) and the partial/page paths to your repo's conventions; the
NEGOTIATION and the 500 status are the standard, not these exact paths.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

logger = logging.getLogger(__name__)


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api")


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") is not None


async def general_exception_handler(request: Request, exc: Exception) -> Response:
    """Handle every UNEXPECTED exception once, at status 500, negotiating the body by request type."""
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        "unhandled_exception",
        exc_info=exc,
        extra={"path": request.url.path, "request_id": request_id},
    )

    if _is_api(request):
        return JSONResponse(
            status_code=500,
            content={
                "error": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "request_id": request_id,
            },
        )

    # wire request.app.state.templates in your app factory (adapt to your template accessor)
    templates: Jinja2Templates = request.app.state.templates
    if _is_htmx(request):
        resp = templates.TemplateResponse(
            request,
            "partials/errors/error.html",
            {"request_id": request_id},
            status_code=500,
        )
        # the client swaps ONLY responses carrying this marker header
        resp.headers["HX-Error-Swap"] = "true"
        return resp

    return templates.TemplateResponse(
        request,
        "pages/500.html",
        {"request_id": request_id},
        status_code=500,
    )
