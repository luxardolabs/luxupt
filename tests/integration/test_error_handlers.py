"""The global error handlers, as `install_error_handlers` wires them on the real app.

A 500 is built outside the security-headers middleware, so the handler sets its own headers; its log
line names the full route template (router prefix included) and the request id the request-logging
middleware minted. A 422 names the fields refused and never the values sent.
"""

import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.views.timelapses_view_service import TimelapsesViewService
from app.web.main import app

SENT_VALUE = "value-the-client-sent-1234"
USERNAME = "erroruser"
PASSWORD = "a-long-enough-password"


@pytest_asyncio.fixture
async def failing_client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    """A signed-in client that receives the app's 500 response instead of the re-raised error."""
    from app.crud.user_crud import user_crud

    await user_crud.create_user(db, username=USERNAME, password=PASSWORD)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        resp = await ac.post(
            "/login", data={"username": USERNAME, "password": PASSWORD}
        )
        assert resp.status_code in (302, 303)
        yield ac


async def _boom(self: TimelapsesViewService) -> None:
    raise RuntimeError("forced failure")


async def test_a_500_carries_the_security_headers_and_logs_the_full_route(
    failing_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(TimelapsesViewService, "get_stats_context", _boom)

    with caplog.at_level(logging.ERROR, logger="app.utils.exception_handlers"):
        resp = await failing_client.get("/timelapses/jobs")

    assert resp.status_code == 500
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Cache-Control"] == "no-store"
    [record] = [r for r in caplog.records if r.getMessage() == "unhandled_exception"]
    assert record.__dict__["route"] == "/timelapses/jobs"
    request_id = record.__dict__["request_id"]
    assert isinstance(request_id, str)
    assert len(request_id) == 32
    assert request_id in resp.text


async def test_every_response_echoes_the_minted_request_id(
    auth_client: AsyncClient,
) -> None:
    first = await auth_client.get("/timelapses/jobs", headers={"X-Request-ID": "mine"})
    second = await auth_client.get("/timelapses/jobs")

    assert first.status_code == 200
    assert first.headers["X-Request-ID"] != "mine"
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


async def test_a_422_names_the_missing_fields_and_never_the_values_sent(
    auth_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.utils.exception_handlers"):
        page = await auth_client.post(
            "/timelapses/historical", data={"camera_id": SENT_VALUE}
        )
        fragment = await auth_client.post(
            "/timelapses/historical",
            data={"camera_id": SENT_VALUE},
            headers={"HX-Request": "true"},
        )

    assert page.status_code == 422
    assert "Invalid Request" in page.text
    assert "body.start_date" in page.text
    assert fragment.status_code == 422
    assert fragment.headers["HX-Error-Swap"] == "true"
    assert "body.start_date" in fragment.text
    for resp in (page, fragment):
        assert SENT_VALUE not in resp.text
    assert SENT_VALUE not in caplog.text
    assert "validation_template_failed" not in caplog.text
