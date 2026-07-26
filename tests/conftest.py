"""Shared test fixtures for luxupt (fastapi-web, sqlite + HTML rendering).

Follows the luxtaste conftest pattern, adapted for luxupt: sqlite instead of
postgres, and an HTML app (render pages) instead of a JSON API.

Env is set BEFORE importing anything under ``src/app``: ``db/connection.py``
builds its engine from ``DATABASE_DIR`` at import time, so setting it here points
the whole app at a throwaway temp sqlite. ``WEB_USERNAME``/``WEB_PASSWORD`` enable
env-auth so the client can log in without seeding DB users.
"""

import os
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest_asyncio

# --- env MUST be set before importing the app (module-level engine reads it) ---
_TMP = Path(tempfile.mkdtemp(prefix="luxupt-test-"))
os.environ.setdefault("DATABASE_DIR", str(_TMP))
os.environ.setdefault("IMAGE_OUTPUT_PATH", str(_TMP / "images"))
os.environ.setdefault("VIDEO_OUTPUT_PATH", str(_TMP / "videos"))
os.environ.setdefault("THUMBNAIL_CACHE_PATH", str(_TMP / "thumbnails"))
os.environ.setdefault("WEB_SESSION_SECRET", "test-only-session-secret-not-for-production")
os.environ.setdefault("WEB_USERNAME", "testadmin")
os.environ.setdefault("WEB_PASSWORD", "test-password-123")

from db.base import Base  # noqa: E402
from db.connection import engine  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from web.main import app  # noqa: E402

TEST_USERNAME = os.environ["WEB_USERNAME"]
TEST_PASSWORD = os.environ["WEB_PASSWORD"]


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema() -> AsyncGenerator[None]:
    """Create the sqlite schema once for the session, dispose the engine after."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    """Unauthenticated ASGI client.

    Uses ASGITransport, which does not run the app lifespan — so the fetch
    service / camera manager never start. ``app.state.templates`` is wired in
    ``create_app()`` (not the lifespan), so rendering still works.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient) -> AsyncClient:
    """Client logged in via env-auth; carries the session cookie on every request."""
    resp = await client.post(
        "/login",
        data={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert resp.status_code in (200, 302, 303), (
        f"login failed: {resp.status_code} — {resp.text[:200]}"
    )
    assert client.cookies, "login did not set a session cookie"
    return client
