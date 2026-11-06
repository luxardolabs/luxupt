# luxarch:exception-handler asset v3 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit exception-handler`.
# luxarch:exception-handler example v1 - emitted asset, re-emit to update; do not hand-edit.
"""Canonical fleet exception handler — ONE content-negotiating handler for UNEXPECTED errors.

Emitted by ``luxarch --emit exception-handler``. Wire it in your app factory:

    from app.utils.exception_handlers import general_exception_handler
    app.add_exception_handler(Exception, general_exception_handler)

The fleet ruling (``luxarch --playbook htmx-error-ux``): a route handler NEVER
wraps its body in a broad ``except Exception`` to render an error partial. That idiom (a) reports a
bug to the user as a normal 200 and to monitoring as a success, and (b) makes ``filterwarnings =
error`` INERT on that route — a raised DeprecationWarning is caught by the route's own ``except`` and
returned 200, so the route-smoke early-warning system silently does nothing on exactly the routes it
exists for. ``fw.route_no_broad_except`` reds that shape.

Instead an unexpected exception PROPAGATES to this single handler, which decides the body by request
type, always at status 500:

  * an ``/api`` or ``/api/…`` path -> JSON (a machine client)
  * an ``HX-Request`` header -> an HTML error PARTIAL + ``HX-Error-Swap: true`` (the client swaps it)
  * otherwise                -> a full HTML error page

Pair it with the client swap: ``luxarch --emit htmx-error-swap`` (HTMX 2 does not swap non-2xx by
default, so without it an HX request would leave the page unchanged). Adapt the template accessor
(``request.app.state.templates`` here) and the partial/page paths to your repo's conventions; the
NEGOTIATION and the 500 status are the standard, not these exact paths.

The log line names the matched route's TEMPLATE (``route``: ``/dashboard/user/{user_id}``, or
``(no route)``), never the request path: a path parameter can carry personal data, and the line is
an ERROR that is kept. Find the request by its ``request_id``, which is logged and echoed only
when it has an id's shape (letters, digits, ``._:-``, up to 64): a middleware that copies a
client's ``X-Request-ID`` would otherwise put whatever the client sent there. A route TEMPLATE is
code: register it from literals, never from data (``app.get(f"/users/{email}")``).
"""

from __future__ import annotations

import logging
import re

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

logger = logging.getLogger(__name__)


# What the 500's log line names instead of the request path: the matched route's template
# (`/dashboard/user/{user_id}`), never its values. A path parameter can hold personal data (an
# email, an end-user id), and an ERROR line is kept and shipped wherever the logs go.
_NO_ROUTE = "(no route)"


def _route_template(request: Request) -> str:
    """The template of the route Starlette matched (`scope["route"]`), or a fixed token when none
    matched or it carries no template: never the request's own path."""
    template = getattr(request.scope.get("route"), "path_format", None)
    return template if isinstance(template, str) and template else _NO_ROUTE


def _is_api(request: Request) -> bool:
    """An `/api` path, or one under `/api/`, below the app's root path: `/apiary` is not one."""
    path = str(request.scope.get("path") or "")
    root = str(request.scope.get("root_path") or "")
    if root and path.startswith(root):
        path = path[len(root) :] or "/"  # the ASGI path holds the app's root path
    return path == "/api" or path.startswith("/api/")


# A request id the app minted is an opaque token. One a middleware copied from a client header
# (`X-Request-ID`) can hold anything, an email included: only an id shape is logged and echoed.
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_UNTRUSTED_ID = "(unrecognised request id)"


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    if value is None:
        return None
    text = str(value)
    return text if _REQUEST_ID.fullmatch(text) else _UNTRUSTED_ID


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") is not None


async def general_exception_handler(request: Request, exc: Exception) -> Response:
    """Handle every UNEXPECTED exception once, at status 500, negotiating the body by request type."""
    request_id = _request_id(request)
    logger.error(
        "unhandled_exception",
        exc_info=exc,
        extra={"route": _route_template(request), "request_id": request_id},
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
        "pages/errors/500.html",
        {"request_id": request_id},
        status_code=500,
    )
