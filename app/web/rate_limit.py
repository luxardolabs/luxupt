"""Inbound request rate limiting — slowapi, the fleet's sanctioned limiter.

`luxarch --doc FLEET-RATE-LIMIT-STANDARD` is the spec; the four decisions it leaves to the repo:

* **It refuses.** A limit that is hit raises ``RateLimitExceeded``, which
  ``rate_limit_exceeded_handler`` turns into a 429 with ``Retry-After`` (§1.1).
* **State is per-process memory, on purpose** (§1.2). luxupt runs ONE uvicorn worker
  (``UVICORN_WORKERS`` defaults to 1 and no deployment raises it) and has no Redis, so a
  process-local store is the shared state. Raising the worker count multiplies every limit by
  it: move the store to Redis first (``storage_uri``).
* **The key is the real client** (§1.3). The app is ``expose``-only behind nginx, which
  resolves the client with ``real_ip`` and forwards it as ``X-Real-IP``. That header is what we
  trust; the FIRST ``X-Forwarded-For`` entry is not, because nginx appends to whatever the
  client sent, so a client can choose it and get a fresh bucket per request.
* **Store-unavailable posture** (§1.5): memory cannot be unavailable, so there is no fail-open
  or fail-closed branch to declare. A Redis store would make this a request limiter that fails
  OPEN.
"""

from typing import cast

from fastapi import Request, Response
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.core import config

# Login: a credential route cannot key on the authenticated identity (the credential is what
# is being verified), so it is per-route plus per-IP (§3). Every attempt counts, success or not.
LOGIN_RATE = (
    f"{config.WEB_LOGIN_RATE_LIMIT} per {config.WEB_LOGIN_RATE_WINDOW_SECONDS} seconds"
)


def client_ip(request: Request) -> str:
    """The client address nginx resolved, or the socket peer when proxy headers are off."""
    if config.WEB_TRUST_PROXY_HEADERS:
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=client_ip)


async def rate_limit_exceeded_handler(request: Request, exc: Exception) -> Response:
    """Refuse an over-limit request: 429 + Retry-After, rendered as the login page."""
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    # slowapi always attaches the Limit it refused on; the window is the honest upper bound.
    retry_after = (
        exc.limit.limit.get_expiry()
        if exc.limit is not None
        else config.WEB_LOGIN_RATE_WINDOW_SECONDS
    )
    templates = request.app.state.templates
    response = cast(
        Response,
        templates.TemplateResponse(
            request,
            "pages/login.html",
            {
                "success": None,
                "error": f"Too many login attempts. Please wait {retry_after} seconds.",
            },
            status_code=429,
        ),
    )
    response.headers["Retry-After"] = str(retry_after)
    return response
