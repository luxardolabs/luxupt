"""Shared test fixtures for luxupt (fastapi-web, sqlite + HTML rendering).

Follows the luxtaste conftest pattern, adapted for luxupt: sqlite instead of
postgres, and an HTML app (render pages) instead of a JSON API.

Env is set BEFORE importing anything under ``app``: ``db/connection.py``
builds its engine from ``DATABASE_DIR`` at import time, so setting it here points
the whole app at a throwaway temp sqlite. ``WEB_USERNAME``/``WEB_PASSWORD`` enable
env-auth so the client can log in without seeding DB users.
"""

import os
import shutil
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

from app.db.base import Base  # noqa: E402
from app.db.connection import async_session, engine  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.web.main import app  # noqa: E402

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
async def db_session() -> AsyncGenerator[AsyncSession]:
    """A DB session on the temp sqlite; rolled back after each test for isolation.

    CRUD helpers flush but don't commit (get_db owns the request transaction), so
    a same-session read sees the flushed rows and the rollback leaves nothing behind.
    """
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest_asyncio.fixture
async def durable_db() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    """Isolated, COMMITTING DB for durability locks (db-mutations playbook §7).

    ``db_session`` rolls back for isolation — useless for proving a write *survives* its
    transaction. This fixture is a fresh per-test sqlite file with its own engine, so a
    test can own a transaction, commit it, then read the row back from a SEPARATE
    connection — only committed data is cross-connection visible, which is the bite that
    proves durability. Open the owner and the reader as two ``maker()`` sessions.

    Mirrors the app maker (``expire_on_commit=False``, ``autoflush=False``) so a service
    method behaves exactly as in production (flush is explicit; the owner commits). The
    temp file is discarded after the test, so real commits never leak across tests.
    """
    tmp = Path(tempfile.mkdtemp(prefix="luxupt-durable-"))
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp / 'durable.db'}")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        test_engine, expire_on_commit=False, autoflush=False
    )
    try:
        yield maker
    finally:
        await test_engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)


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
