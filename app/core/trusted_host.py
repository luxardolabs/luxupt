# luxarch:trusted-host asset v3 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit trusted-host`.
"""The app refuses a Host header it does not serve: a required, per-environment ALLOWED_HOSTS.

Emitted by ``luxarch --emit trusted-host``. Drop it in at ``app/core/trusted_host.py``; the standard
is ``luxarch --doc FLEET-BUILD-DEPLOY-STANDARD`` "Trusted hosts", enforced by ``fw.trusted_host_enforced``.

Why: anything the app builds from the request's Host (a password-reset or magic link, a redirect, an
absolute URL in an email) points wherever the client says. Send ``Host: attacker.example`` with a
victim's email address and the victim receives a genuine email whose link carries their token to the
attacker. The app has to refuse a Host it does not serve, whatever sits in front of it.

Wire it in two places:

    # app/core/config.py: REQUIRED, no default. An unset, empty or `*` value refuses to start.
    from app.core.trusted_host import AllowedHosts

    class Settings(BaseSettings):
        ALLOWED_HOSTS: AllowedHosts

    # app/main.py: on the app the entry point serves
    from app.core.trusted_host import AllowedHostsMiddleware

    app.add_middleware(
        AllowedHostsMiddleware,
        allowed_hosts=settings.ALLOWED_HOSTS,
        health_paths=("/health",),  # the paths the container's own HEALTHCHECK requests
    )

The hostnames are environment data, never code: ``ALLOWED_HOSTS=dev.example.com`` in ``.env.dev``,
``ALLOWED_HOSTS=example.com,www.example.com`` in ``.env.prod``, and the key, with a placeholder, in
the committed ``.env.example``. Comma-separated; ``*.example.com`` admits that domain's subdomains.
Each entry is a hostname or an IPv4 address and nothing else: an entry with a port, a scheme or path,
brackets, quotes or whitespace (the JSON-list form ``["a.example.com","b.example.com"]``), or an IPv6
literal can never match a request, so it refuses to start and names the entry. An IPv6 literal is
refused because a request for one carries ``Host: [fd00::10]:9000``, and the comparison Starlette runs
on the versions the fleet locks reads the text before the first ``:``; serve it by a hostname.

A request whose Host header is not a hostname (or a bracketed IPv6 address) plus an optional ``:port``
of digits gets 400 before anything is matched: Starlette compares only the part before the first
``:``, so ``Host: localhost:@evil.example`` would otherwise pass on ``localhost``.

The container's own HEALTHCHECK requests ``http://localhost:<port>/health`` from inside the
container, so its Host is ``localhost``, which ALLOWED_HOSTS does not list. Rather than allowing
``localhost`` everywhere (a client could then send ``Host: localhost`` to any route), exactly one
request shape passes without the check: a loopback peer, a loopback Host, one of
``health_paths``, and no forwarding header. Behind uvicorn ``--proxy-headers`` the peer is whatever
``X-Forwarded-For`` resolved to, so a request carrying any forwarding header (``X-Forwarded-For``,
``Forwarded``, ``X-Real-IP``) is checked like every other request, whatever its peer claims.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from typing import Annotated

from pydantic import BeforeValidator
from pydantic_settings import NoDecode
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ENV_KEY = "ALLOWED_HOSTS"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_FORWARDING = frozenset(
    {
        b"x-forwarded-for",
        b"forwarded",
        b"x-real-ip",
        b"x-forwarded-host",
        b"x-forwarded-proto",
    }
)


# v3: what an entry may be, a hostname or an IPv4 address (optionally `*.` for the subdomains), and
# what a request's Host header may be, that name or a bracketed IPv6 address, then `:port` of digits.
_ENTRY_RE = re.compile(r"(?:\*\.)?[a-z0-9_-]+(?:\.[a-z0-9_-]+)*")
_HOST_HEADER_RE = re.compile(
    r"(?:(?P<name>[A-Za-z0-9._-]+)|\[(?P<ipv6>[0-9A-Fa-f:.]+)\])(?::(?P<port>[0-9]{1,5}))?"
)
_JSON_FIX = f"{ENV_KEY}=a.example.com,b.example.com"


class AllowedHostsError(ValueError):
    """ALLOWED_HOSTS is empty, answers every Host, or lists an entry that can never match; the app
    must not start with it."""


def parse_allowed_hosts(value: object) -> list[str]:
    """The hostnames in ``value`` (a comma-separated string, or a list of them).

    Raises AllowedHostsError when none is left, one of them is ``*``, or one can never match a
    request (a port, a scheme or path, brackets, quotes or whitespace, an IPv6 literal): the app
    would answer a Host it does not serve, or none at all, so it refuses to start instead.
    """
    if isinstance(value, str):
        items: list[object] = list(value.split(","))
    elif isinstance(value, list | tuple):
        items = list(value)
    else:
        raise AllowedHostsError(
            f"{ENV_KEY} must be a comma-separated list of hostnames, "
            f"not {type(value).__name__}: set it in .env.<env>"
        )
    hosts: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise AllowedHostsError(
                f"{ENV_KEY} holds {item!r}, which is not a hostname: set it in .env.<env>"
            )
        # v2: lowercased (a proxy's `$host` is, and the comparison is exact).
        host = item.strip().lower()
        if not host:
            continue
        if host != "*" and not _ENTRY_RE.fullmatch(host):
            raise AllowedHostsError(_why_not_a_host(host))
        hosts.append(host)
    if not hosts:
        raise AllowedHostsError(
            f"{ENV_KEY} is empty: set this environment's hostnames in .env.<env> "
            f"(e.g. {ENV_KEY}=dev.example.com); the app does not start without them"
        )
    if "*" in hosts:
        raise AllowedHostsError(
            f"{ENV_KEY} contains '*', which answers every Host header: list this "
            "environment's hostnames in .env.<env> instead"
        )
    return hosts


def _why_not_a_host(host: str) -> str:
    """Why `host` can never match a request, naming the entry and the fix (v3)."""
    if _is_ipv6_literal(host):
        return (
            f"{ENV_KEY} lists {host!r}, an IPv6 literal, which never matches: a request for it "
            "carries `Host: [addr]:port` and the comparison reads the text before the first ':'. "
            "Serve it by a hostname and list that in .env.<env>"
        )
    if any(c in host for c in "[]\"'") or any(c.isspace() for c in host):
        return (
            f"{ENV_KEY} lists {host!r}, which is not a hostname: the value is a comma-separated "
            "list with no brackets, quotes or spaces (not the JSON-list form). Write "
            f"{_JSON_FIX} in .env.<env>"
        )
    if "/" in host:
        return (
            f"{ENV_KEY} lists {host!r}: hostname only, no scheme or path (no 'https://', no "
            "'/'), which never matches. Write the bare hostname in .env.<env>"
        )
    if ":" in host:
        # v2: Starlette compares the Host without its port, so `example.com:18000` never matched
        # and an app that started cleanly refused every request.
        return (
            f"{ENV_KEY} lists {host!r} with a port, which never matches (the port is not part "
            f"of the comparison): drop ':{host.rsplit(':', 1)[1]}' in .env.<env>"
        )
    return (
        f"{ENV_KEY} lists {host!r}, which is not a hostname (letters, digits, '.', '-' and '_', "
        "or a leading '*.' for the subdomains): fix it in .env.<env>"
    )


def _is_ipv6_literal(host: str) -> bool:
    """`::1`, `fd00::10`, `[fd00::10]` or `[fd00::10]:9000`."""
    inner = host[1:].split("]", 1)[0] if host.startswith("[") else host
    try:
        return isinstance(ipaddress.ip_address(inner), ipaddress.IPv6Address)
    except ValueError:
        return False


# The settings field type. NoDecode keeps pydantic-settings from reading the env value as JSON, so
# `ALLOWED_HOSTS=a.example.com,b.example.com` is the whole syntax.
AllowedHosts = Annotated[list[str], NoDecode, BeforeValidator(parse_allowed_hosts)]


def _loopback_peer(scope: Scope) -> bool:
    """The TCP peer is this machine: the container's own HEALTHCHECK, never a proxied request."""
    client = scope.get("client")
    if not client:
        return False
    try:
        return ipaddress.ip_address(str(client[0])).is_loopback
    except ValueError:
        return False


def _host_name(raw: str) -> str | None:
    """The hostname of a Host header value, without its port (`[::1]:8000` -> `::1`), or None when
    the value is not a hostname or a bracketed IPv6 address followed by an optional `:port` (v3)."""
    m = _HOST_HEADER_RE.fullmatch(raw)
    if m is None or (m["port"] is not None and int(m["port"]) > 65535):
        return None
    if m["ipv6"] is None:
        return m["name"]
    try:
        return ipaddress.IPv6Address(m["ipv6"]).compressed
    except ValueError:
        return None


def _host_header(scope: Scope) -> str:
    """The request's first Host header ("" when it sends none), the one Starlette reads."""
    for key, value in scope.get("headers", []):
        if key == b"host":
            return str(value.decode("latin-1"))
    return ""


class AllowedHostsMiddleware(TrustedHostMiddleware):
    """Starlette's TrustedHostMiddleware over a validated list, plus the one in-container probe.

    A request whose Host is malformed, or not in ``allowed_hosts``, gets 400. The exception is a loopback peer
    sending a loopback Host to one of ``health_paths``: the container's own HEALTHCHECK.
    """

    def __init__(
        self,
        app: ASGIApp,
        allowed_hosts: Sequence[str],
        health_paths: Sequence[str] = ("/health",),
        www_redirect: bool = True,
    ) -> None:
        super().__init__(
            app,
            allowed_hosts=parse_allowed_hosts(list(allowed_hosts)),
            www_redirect=www_redirect,
        )
        self.health_paths = tuple(health_paths)

    def _in_container_probe(self, scope: Scope) -> bool:
        if not _loopback_peer(scope):
            return False
        for key, _value in scope.get("headers", []):
            # v2: a forwarding header means a proxy relayed this request. Behind uvicorn
            # `--proxy-headers`, the "peer" is then whatever X-Forwarded-For resolved to, and a
            # client could make it 127.0.0.1. The container's own HEALTHCHECK never sends one.
            if key in _FORWARDING:
                return False
        host = _host_name(_host_header(scope))
        path = str(scope.get("path", ""))
        return host in _LOOPBACK_HOSTS and any(
            path == p or path.startswith(p.rstrip("/") + "/") for p in self.health_paths
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # v3: a Host that is not a hostname and an optional `:port` is refused before matching.
        # Starlette compares only the text before the first ':', so `localhost:@evil.example`
        # passed on `localhost`, and URLs built from it fell back to the container's own address.
        if (
            scope["type"] in ("http", "websocket")
            and _host_name(_host_header(scope)) is None
        ):
            await PlainTextResponse("Invalid host header", status_code=400)(
                scope, receive, send
            )
            return
        if scope["type"] == "http" and self._in_container_probe(scope):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)
