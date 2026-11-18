# luxarch:exception-handler asset v5 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit exception-handler`.
# luxarch:exception-handler example v1 - emitted asset, re-emit to update; do not hand-edit.
"""Canonical fleet exception handler — ONE content-negotiating handler for UNEXPECTED errors.

Emitted by ``luxarch --emit exception-handler``. Wire it in your app factory, after every sub-app is mounted:

    from app.utils.exception_handlers import install_error_handlers
    install_error_handlers(app)

``install_error_handlers`` registers ``general_exception_handler`` (below) and
``validation_exception_handler`` on the app AND on every FastAPI/Starlette sub-app mounted on it, at any
depth. A mounted sub-app has its own outermost middleware, which answers its own 500 with a plain-text
body and none of ``ERROR_HEADERS`` unless the handler is registered on it too; the helper also records the
mount's template for the log line, and a sub-app without ``state.templates`` uses the app's. Registering by
hand, on the app alone, covers the app's own routes only.

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

A request that FAILS VALIDATION (``RequestValidationError``) is not unexpected, so it has its own handler,
``validation_exception_handler`` (``install_error_handlers`` wires it). It answers 422 by the same negotiation (JSON ``{"error": "VALIDATION_ERROR", "errors": [{"loc", "type"}]}``
for an API path; ``partials/errors/validation.html`` for an HTMX request, with ``HX-Error-Swap``;
``pages/errors/422.html`` otherwise; each template receives ``request_id`` and ``errors``, a list of
``{"loc": "body.email", "type": "missing"}``). It logs ``request_validation_failed`` at WARNING with
the route template, the request id and ``fields`` (``["body.email:missing"]``), and adds a span event
of the same name when OpenTelemetry is importable. It NEVER echoes the submitted input: FastAPI's
default 422 body carries each rejected value (a password included) in ``input``, and a message can
quote one, so only the field path and the error type leave the handler. Only a name the route's
model DECLARES is written: a dict key, an extra field the client sent, a union tag, and everything
below one, is written ``(key)``. A template that cannot be rendered (a repo that wired the handler and
has no ``422`` template yet) answers the JSON 422 instead, and the failure is logged without its
traceback.

A traceback whose exception chain holds a FastAPI ``ValidationException`` (request, response and websocket
validation errors) or a pydantic ``ValidationError`` is never logged with that chain: their text quotes the submitted input. The line keeps the
frames and names the exception type; the message is withheld.

Limits, stated because no static reader can close them: a validation error's text that the application
puts in a string itself (``f"failed: {err}"``, ``str(err)``) is already text when the log call is made,
and reaches the line unchanged; and OpenTelemetry's ``exception.message`` span attribute carries the
text of an exception recorded on a span (the telemetry asset's ``configure_tracing(exception_text="withhold")`` is the one place that
withholds it). A websocket whose connection fails validation closes with code 1008 and field paths and
error types only.

Pair it with the client swap: ``luxarch --emit htmx-error-swap`` (HTMX 2 does not swap non-2xx by
default, so without it an HX request would leave the page unchanged). Adapt the template accessor
(``request.app.state.templates`` here) and the partial/page paths to your repo's conventions; the
NEGOTIATION and the 500 status are the standard, not these exact paths.

The log line names the matched route's TEMPLATE (``route``: ``/dashboard/user/{user_id}``, or
``(no route)``), never the request path: a path parameter can carry personal data, and the line is
an ERROR that is kept. The template includes every router prefix and mount in front of the route.
Find the request by its ``request_id``, which is read from ``request.state.request_id`` (set it in
your request-id middleware; with none set it is ``None``) and is logged and echoed only
when it has an id's shape (letters, digits, ``._:-``, up to 64): a middleware that copies a
client's ``X-Request-ID`` would otherwise put whatever the client sent there. A route TEMPLATE is
code: register it from literals, never from data (``app.get(f"/users/{email}")``).

A 500 is built by Starlette's outermost middleware, outside your security-headers middleware, so the
handler sets ``ERROR_HEADERS`` itself (no framing, no sniffing, no caching, no referrer) on every
500 it returns; a header the template already set is kept.
"""

from __future__ import annotations

import collections.abc
import importlib
import logging
import re
import types
import typing
from typing import Any

from fastapi import Request, WebSocket
from fastapi.exceptions import (
    RequestValidationError,
    ValidationException,
    WebSocketRequestValidationError,
)
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.requests import HTTPConnection
from starlette.responses import Response
from starlette.routing import Host, Mount

logger = logging.getLogger(__name__)


def _chain_holds_input(exc: BaseException | None, seen: set[int]) -> bool:
    """Whether ``exc`` or anything in its cause/context chain (or exception group) quotes input."""
    if exc is None or id(exc) in seen:
        return False
    seen.add(id(exc))
    if isinstance(exc, ValidationException | ValidationError):
        return True
    members = getattr(exc, "exceptions", None)
    inner = (
        [m for m in members if isinstance(m, BaseException)]
        if isinstance(members, tuple)
        else []
    )
    return any(
        _chain_holds_input(e, seen) for e in [exc.__cause__, exc.__context__, *inner]
    )


def _loggable(exc: BaseException) -> Any:
    """``exc`` for ``exc_info=``, or, when its chain holds a validation error, the same frames under a
    message that names the type and withholds the text (it quotes the submitted input)."""
    if not _chain_holds_input(exc, set()):
        return exc
    kept = RuntimeError(
        f"{type(exc).__qualname__} (message withheld: its chain holds a validation error's input)"
    )
    return (RuntimeError, kept, exc.__traceback__)


# What the 500's log line names instead of the request path: the matched route's template
# (`/dashboard/user/{user_id}`), never its values. A path parameter can hold personal data (an
# email, an end-user id), and an ERROR line is kept and shipped wherever the logs go.
_NO_ROUTE = "(no route)"


# What formatting a route template with the request's parameters can raise.
_TEMPLATE_ERRORS = (KeyError, IndexError, ValueError)


_PARAM_NAME = re.compile(r"\{(\w+)\}")
# Where `install_error_handlers` records the template of the mount a sub-app sits under.
_MOUNT_REMAINDER = re.compile(
    r"/\{path\}$"
)  # the tail parameter Starlette adds to a mount
_MOUNT_ATTR = "error_handler_mount_template"
_LOGGED = "error_handler_logged"  # a flag in the request scope
_ROOT_ATTR = "error_handler_root"
_UNWRAP_LIMIT = 16  # wrappers to look through for a mounted Starlette app


def _prefix_template(
    request: HTTPConnection, route: object, relative: str
) -> str | None:
    """The proxy root, mounts and router prefixes in front of ``relative`` (the matched route's own
    template), with each path parameter among them written as ``{name}``; ``None`` when they cannot be
    read back from the request without guessing.

    A mount's template is the one ``install_error_handlers`` recorded on the sub-app: a mount's
    parameter shares ``path_params`` with the route's, and one named like the route's overwrites it, so
    its value is not recoverable from the request. A mount with no recorded template is unreadable."""
    scope = request.scope
    params = scope.get("path_params") or {}
    own = set(getattr(route, "param_convertors", None) or {})
    try:
        tail = relative.format_map({name: str(params[name]) for name in own})
    except _TEMPLATE_ERRORS:
        return None
    path = str(scope.get("path") or "")
    root = str(scope.get("root_path") or "")
    base = str(scope.get("app_root_path") or "")
    if not (path.startswith(root) and root.startswith(base)):
        return None
    mounted = ""
    skip = set(own)
    if len(root) > len(base):
        mounted = (
            getattr(getattr(scope.get("app"), "state", None), _MOUNT_ATTR, None) or ""
        )
        if not mounted:
            return None
        skip |= set(_PARAM_NAME.findall(mounted))
        skip.add("path")  # Starlette's own remainder parameter of a mount
    rest = path[len(root) :]
    if not rest.endswith(tail):
        return None
    prefix = rest[: len(rest) - len(tail)]
    out: list[str] = [base, mounted]
    pos = 0
    for name, value in params.items():
        if name in skip:
            continue
        text = str(value)
        at = prefix.find(text, pos) if text else -1
        if at < 0:
            return None
        out.append(prefix[pos:at])
        out.append("{" + name + "}")
        pos = at + len(text)
    out.append(prefix[pos:])
    return "".join(out)


def _route_template(request: HTTPConnection) -> str:
    """The template of the route Starlette matched (`scope["route"]`) with the prefixes of the
    routers and mounts it sits under, or a fixed token when none matched or it carries no template:
    never the request's own path. The matched route's own template is relative to its router, so
    the prefix comes from the request's path, where each prefix parameter's value is written as its
    name; a prefix value that equals an earlier literal segment of the prefix is written at that
    segment, which exposes nothing the literal does not. A prefix that cannot be read back that way
    leaves the route's own template."""
    route = request.scope.get("route")
    template = getattr(route, "path_format", None)
    if not isinstance(template, str) or not template:
        return _NO_ROUTE
    prefix = _prefix_template(request, route, template)
    return template if prefix is None else prefix + template


def _is_api(request: Request) -> bool:
    """An `/api` path, or one under `/api/`, below the proxy's root path: `/apiary` is not one, and
    a sub-app mounted at `/api` is."""
    scope = request.scope
    path = str(scope.get("path") or "")
    base = scope.get("app_root_path")
    root = str(scope.get("root_path") or "") if base is None else str(base)
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


# A 500 is built by Starlette's outermost middleware, outside the app's own headers middleware, so
# the handler sets these itself. `setdefault` keeps one a template already set.
ERROR_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _with_error_headers(response: Response) -> Response:
    for name, value in ERROR_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") is not None


async def general_exception_handler(request: Request, exc: Exception) -> Response:
    """Handle every UNEXPECTED exception once, at status 500, negotiating the body by request type."""
    request_id = _request_id(request)
    # An exception escaping a mounted sub-app reaches the handler of each app on its way out, with
    # the one request scope: the request is logged once.
    if not request.scope.get(_LOGGED):
        request.scope[_LOGGED] = True
        logger.error(
            "unhandled_exception",
            exc_info=_loggable(exc),
            extra={"route": _route_template(request), "request_id": request_id},
        )

    if _is_api(request):
        return _error_json(request_id)

    htmx = _is_htmx(request)
    name = "partials/errors/error.html" if htmx else "pages/errors/500.html"
    resp, failure = _render(request, name, {"request_id": request_id}, 500)
    if resp is None:
        if failure is not None:
            logger.error(
                "error_template_failed",
                extra={"template": name, "failure": failure},
            )
        return _error_json(request_id)
    if htmx:
        # the client swaps ONLY responses carrying this marker header
        resp.headers["HX-Error-Swap"] = "true"
    return _with_error_headers(resp)


def _error_json(request_id: str | None) -> Response:
    return _with_error_headers(
        JSONResponse(
            status_code=500,
            content={
                "error": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "request_id": request_id,
            },
        )
    )


def _templates(request: Request) -> Jinja2Templates | None:
    """The sub-app's ``state.templates``, else the root app's, looked up now: a handler installed
    before the templates were set still finds them."""
    app = request.app
    for candidate in (app, getattr(app.state, _ROOT_ATTR, None)):
        found = getattr(getattr(candidate, "state", None), "templates", None)
        if isinstance(found, Jinja2Templates):
            return found
    return None


def _render(
    request: Request, name: str, context: dict[str, Any], status: int
) -> tuple[Response | None, str | None]:
    """The template response, or ``None`` and why not: no templates at all (``None``), or the type of
    what rendering raised (kept out of the traceback, which would chain a validation error)."""
    templates = _templates(request)
    if templates is None:
        return None, None
    try:
        return templates.TemplateResponse(
            request, name, context, status_code=status
        ), None
    except Exception as err:
        return None, type(err).__qualname__


# The kind of a validation error is the library's identifier (`missing`, `extra_forbidden`).
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_KEY = "(key)"
_SEQUENCES = (list, set, frozenset, collections.abc.Sequence, collections.abc.Set)


def _members(tp: object) -> list[object]:
    """The concrete types a (possibly Annotated / Optional / union) annotation allows."""
    origin = typing.get_origin(tp)
    if origin is typing.Annotated:
        return _members(typing.get_args(tp)[0])
    if origin is typing.Union or origin is types.UnionType:
        return [m for arg in typing.get_args(tp) for m in _members(arg)]
    return [tp]


def _declared_name(tp: object, name: str) -> tuple[bool, object]:
    """The annotation of the field ``name`` of a model in ``tp``, when one DECLARES it."""
    for member in _members(tp):
        if isinstance(member, type) and issubclass(member, BaseModel):
            for key, info in member.model_fields.items():
                if name == key or name == info.alias:
                    return True, info.annotation
    return False, None


def _declared_index(tp: object, index: int) -> tuple[bool, object]:
    """The element annotation of a list/tuple/set in ``tp`` (an index is the caller's position, not a name)."""
    for member in _members(tp):
        origin = typing.get_origin(member)
        args = typing.get_args(member)
        if origin is tuple and args:
            if len(args) == 2 and args[1] is Ellipsis:
                return True, args[0]
            if 0 <= index < len(args):
                return True, args[index]
        elif isinstance(origin, type) and issubclass(origin, _SEQUENCES) and args:
            return True, args[0]
    return False, None


_SOURCES = {
    "query": "query_params",
    "header": "header_params",
    "cookie": "cookie_params",
    "path": "path_params",
    "body": "body_params",
}


def _inputs(request: HTTPConnection) -> dict[str, list[Any]]:
    """The route's declared inputs by source (``query``, ``header``, ``cookie``, ``path``, ``body``),
    sub-dependencies included; empty when the route is not one that declares them."""
    found: dict[str, list[Any]] = {source: [] for source in _SOURCES}
    todo = [getattr(request.scope.get("route"), "dependant", None)]
    while todo:
        dependant = todo.pop()
        if dependant is None:
            continue
        for source, attribute in _SOURCES.items():
            found[source].extend(getattr(dependant, attribute, None) or [])
        todo.extend(getattr(dependant, "dependencies", None) or [])
    return found


def _field_type(field: Any) -> object:
    return getattr(getattr(field, "field_info", None), "annotation", None)


def _safe_loc(loc: object, inputs: dict[str, list[Any]]) -> str:
    """A validation error's location with every element the route's model does not DECLARE written
    ``(key)``, and everything below such an element too: a dict key, an extra field, a union tag, an
    index into anything but a list/tuple/set."""
    if not isinstance(loc, tuple | list) or not loc:
        return _KEY
    source = str(loc[0])
    if source not in inputs:
        return ".".join([_KEY] * len(loc))
    parts = [source]
    fields = inputs[source]
    rest = list(loc[1:])
    current: object = None
    known = True
    if (
        source == "body"
        and len(fields) == 1
        and not getattr(fields[0].field_info, "embed", False)
    ):
        current = _field_type(fields[0])
    elif rest:
        first = rest.pop(0)
        match = [
            f
            for f in fields
            if isinstance(first, str) and first in {f.name, getattr(f, "alias", None)}
        ]
        known = bool(match)
        parts.append(str(first) if known else _KEY)
        current = _field_type(match[0]) if match else None
    for element in rest:
        if known and isinstance(element, str):
            known, current = _declared_name(current, element)
        elif known and isinstance(element, int) and not isinstance(element, bool):
            known, current = _declared_index(current, element)
        else:
            known = False
        parts.append(str(element) if known else _KEY)
    return ".".join(parts)


def _safe_errors(
    request: HTTPConnection, exc: ValidationException
) -> list[dict[str, str]]:
    """Each error as ``{"loc": "body.email", "type": "missing"}``: the field path and the error type,
    never ``input``, ``ctx``, ``msg`` or ``url`` (they quote the submitted value)."""
    inputs = _inputs(request)
    out: list[dict[str, str]] = []
    for err in exc.errors():
        kind = str(err.get("type", ""))
        out.append(
            {
                "loc": _safe_loc(err.get("loc"), inputs),
                "type": kind if _FIELD.fullmatch(kind) else "invalid",
            }
        )
    return out


def _record_on_span(errors: list[dict[str, str]]) -> None:
    """Add a ``request_validation_failed`` event to the current span when OpenTelemetry is importable."""
    try:
        trace = importlib.import_module("opentelemetry.trace")
    except ImportError:
        return
    trace.get_current_span().add_event(
        "request_validation_failed",
        {"validation.fields": [f"{e['loc']}:{e['type']}" for e in errors]},
    )


def _validation_json(errors: list[dict[str, str]], request_id: str | None) -> Response:
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "message": "The request was invalid",
            "errors": errors,
            "request_id": request_id,
        },
    )


async def validation_exception_handler(request: Request, exc: Exception) -> Response:
    """Handle a ``RequestValidationError`` once, at status 422, negotiating the body by request type
    and never echoing the submitted input."""
    if not isinstance(exc, RequestValidationError):
        raise exc
    errors = _safe_errors(request, exc)
    request_id = _request_id(request)
    route = _route_template(request)
    fields = [f"{e['loc']}:{e['type']}" for e in errors]
    logger.warning(
        "request_validation_failed",
        extra={"route": route, "request_id": request_id, "fields": fields},
    )
    _record_on_span(errors)

    if _is_api(request):
        return _validation_json(errors, request_id)

    htmx = _is_htmx(request)
    name = "partials/errors/validation.html" if htmx else "pages/errors/422.html"
    resp, failure = _render(
        request, name, {"request_id": request_id, "errors": errors}, 422
    )
    if resp is None:
        if failure is not None:
            # No traceback: it would chain the validation error, whose text quotes the input.
            logger.error(
                "validation_template_failed",
                extra={"route": route, "template": name, "failure": failure},
            )
        return _validation_json(errors, request_id)
    if htmx:
        resp.headers["HX-Error-Swap"] = "true"
    return resp


_WS_POLICY_VIOLATION = 1008
_WS_REASON_BYTES = 123  # a close frame's reason is at most 123 bytes


async def websocket_validation_exception_handler(
    websocket: WebSocket, exc: Exception
) -> None:
    """Close a websocket whose connection failed validation with code 1008 and a fixed reason that
    names field paths and error types only, never the input (FastAPI's default reason is the full
    error list, input included)."""
    errors = (
        _safe_errors(websocket, exc) if isinstance(exc, ValidationException) else []
    )
    fields = [f"{e['loc']}:{e['type']}" for e in errors]
    logger.warning(
        "request_validation_failed",
        extra={"route": _route_template(websocket), "fields": fields},
    )
    reason = ("invalid: " + ", ".join(fields)).encode()[:_WS_REASON_BYTES]
    await websocket.close(
        code=_WS_POLICY_VIOLATION, reason=reason.decode(errors="ignore")
    )


def install_error_handlers(app: Starlette) -> None:
    """Register the handlers on ``app`` and on every FastAPI/Starlette sub-app mounted on it, at any
    depth, so a 500 or a 422 raised below a mount gets the same negotiated body, headers and log line.

    Call it once, after the sub-apps are mounted. It walks ``Mount`` and ``Host`` routes, looks
    through a mount's ``middleware=`` or any ASGI wrapper that exposes ``.app`` / ``_base_app``, and
    into the routes of a mount built from ``routes=``. A mounted app that is not Starlette (a
    static-files app, a bare ASGI callable) needs no handler of its own: the app's handler answers
    its 500. One that cannot be looked through within ``_UNWRAP_LIMIT`` wrappers (or loops) is listed
    in one warning. Templates are looked up when a request fails, on the sub-app and then on this
    app, so the order of ``app.state.templates`` and this call does not matter. Each sub-app also
    records the template of its mount for the log line."""
    skipped: list[str] = []
    _install(app, app, "", skipped)
    if skipped:
        logger.warning("error_handlers_not_installed", extra={"mounts": skipped})


def _unwrap(app: object) -> tuple[Starlette | None, list[Any] | None, bool]:
    """Look through ``_base_app`` / ``.app`` wrappers: the Starlette app inside, else the routes of a
    router, else neither; the last value is true when the depth limit was reached."""
    for _ in range(_UNWRAP_LIMIT):
        if isinstance(app, Starlette):
            return app, None, False
        routes = getattr(app, "routes", None)
        if isinstance(routes, list):
            return None, routes, False
        app = getattr(app, "_base_app", None) or getattr(app, "app", None)
        if app is None:
            return None, None, False
    return None, None, True


def _starlette_app(app: object) -> Starlette | None:
    """The Starlette app inside ``app``, through ``_base_app`` / ``.app`` wrappers, or ``None``."""
    return _unwrap(app)[0]


def _install(root: Starlette, app: Starlette, prefix: str, skipped: list[str]) -> None:
    app.add_exception_handler(Exception, general_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(
        WebSocketRequestValidationError, websocket_validation_exception_handler
    )
    _walk(root, app.routes, prefix, skipped)


def _walk(root: Starlette, routes: list[Any], prefix: str, skipped: list[str]) -> None:
    for route in routes:
        if not isinstance(route, Mount | Host):
            continue
        # a Host adds no path to the request; a Mount adds its template
        here = prefix
        if isinstance(route, Mount):
            here += _MOUNT_REMAINDER.sub("", str(getattr(route, "path_format", "")))
        sub, inner, too_deep = _unwrap(getattr(route, "_base_app", None) or route.app)
        if sub is not None:
            setattr(sub.state, _MOUNT_ATTR, here)
            setattr(sub.state, _ROOT_ATTR, root)
            _install(root, sub, here, skipped)
        elif inner is not None:
            _walk(root, inner, here, skipped)
        elif too_deep:
            skipped.append(f"{here or '/'}: {type(route.app).__qualname__}")
