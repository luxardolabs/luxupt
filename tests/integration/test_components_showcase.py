"""Render coverage for the dev-only component showcase (/system/components).

The showcase must actually *render* every macro — including the data-bound cards
and pagination fed by SystemViewService.get_components_context() — so a broken
macro call or a filter type-mismatch surfaces here as a 500 instead of in prod.
The page is gated on LOGGING_LEVEL == "DEBUG"; monkeypatch flips that per-test.
"""

import pytest
from httpx import AsyncClient

from app import config


class TestComponentShowcase:
    async def test_page_renders_in_debug(
        self, auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "LOGGING_LEVEL", "DEBUG")
        resp = await auth_client.get("/system/components", follow_redirects=True)
        assert resp.status_code == 200, resp.text[:400]
        assert "text/html" in resp.headers["content-type"]
        # Data-bound macros rendered from the sample objects (camera_card etc.).
        assert "Component Library" in resp.text
        assert "Front Door" in resp.text

    async def test_page_hidden_without_debug(
        self, auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "LOGGING_LEVEL", "INFO")
        resp = await auth_client.get("/system/components", follow_redirects=True)
        assert resp.status_code == 404

    @pytest.mark.parametrize("variant", ["drawer", "shell"])
    async def test_panel_demo_partial_renders(
        self,
        auth_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        variant: str,
    ) -> None:
        monkeypatch.setattr(config, "LOGGING_LEVEL", "DEBUG")
        resp = await auth_client.get(f"/system/components/panel-demo?variant={variant}")
        assert resp.status_code == 200, resp.text[:400]
        assert "text/html" in resp.headers["content-type"]
