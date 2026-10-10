"""A Protect URL that answers with a web page, not the integration API, is reported as exactly that.

Seen in the field (LUXUPT-86): BB's Protect moved to another console, the old console still ran
UniFi OS, and every `/proxy/protect/integration/v1/*` request came back 200 text/html, its web
UI. `response.json()` then surfaced as "Expecting value: line 1 column 1 (char 0)" on /health,
which says nothing about the cause.
"""

import httpx
import pytest

from app.clients.camera_manager import (
    CameraManager,
    CameraManagerSettings,
    ProtectApiUnavailableError,
)

BASE_URL = "https://console.example.test/proxy/protect/integration/v1"


def _manager(handler: httpx.MockTransport) -> CameraManager:
    manager = CameraManager(
        CameraManagerSettings(
            base_url=BASE_URL,
            api_key="k",
            verify_ssl=False,
            request_timeout=5,
            rate_limit=10,
            rate_limit_buffer=0.8,
            min_offset_seconds=1,
            max_offset_seconds=30,
            camera_refresh_interval=300,
        )
    )
    manager.client = httpx.AsyncClient(transport=handler)
    return manager


async def test_html_from_protect_url_names_the_problem() -> None:
    page = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            text="<!doctype html><html></html>",
            headers={"content-type": "text/html"},
        )
    )
    manager = _manager(page)
    with pytest.raises(ProtectApiUnavailableError) as raised:
        await manager.refresh_cameras(force=True)
    message = str(raised.value)
    assert "text/html" in message
    assert "console.example.test" in message
    await manager.client.aclose()


async def test_json_from_protect_url_still_discovers_cameras() -> None:
    api = httpx.MockTransport(lambda _: httpx.Response(200, json=[]))
    manager = _manager(api)
    assert await manager.refresh_cameras(force=True) == []
    await manager.client.aclose()
