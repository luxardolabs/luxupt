"""A request whose Host header is not one of ALLOWED_HOSTS is refused before any route runs.

nginx forwards the client's Host unchanged (`proxy_set_header Host $host`), so without this the
app answers any name pointed at it: links and redirects built from the request carry a host an
attacker chose. `make smoke` probes the same thing on the deployed stack.
"""

import logging

import pytest
from httpx import AsyncClient

from app.core.config import parse_allowed_hosts


async def test_forged_host_is_refused(client: AsyncClient) -> None:
    resp = await client.get("/health/live", headers={"Host": "smoke-forged.invalid"})
    assert resp.status_code == 400


async def test_allowed_host_is_served(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.status_code == 200


async def test_forged_host_refusal_is_logged(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Every other 4xx is logged by RequestLoggingMiddleware; a refusal must not be the silent one.
    with caplog.at_level(logging.INFO, logger="app.web.middleware"):
        await client.get("/health/live", headers={"Host": "smoke-forged.invalid"})
    assert any(getattr(r, "status_code", None) == 400 for r in caplog.records), (
        "forged-Host refusal left no log line"
    )


def test_naming_a_host_keeps_localhost_for_the_healthcheck() -> None:
    # The container healthcheck hits localhost:${WEB_PORT}; naming the public host must not drop it.
    hosts = parse_allowed_hosts("ll01.example.com")
    assert {"ll01.example.com", "localhost", "127.0.0.1"} <= set(hosts)


def test_unset_falls_back_closed_to_localhost() -> None:
    assert set(parse_allowed_hosts("")) == {"localhost", "127.0.0.1"}
