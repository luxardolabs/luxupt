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

# --- the dedicated test database (luxlint test.db_isolated) ---------------------------
# ONE fleet mechanism: the suite sources a dedicated TEST_DATABASE_URL with a throwaway
# default -- NEVER the app's DATABASE_DIR/DATABASE_URL. `make test` provides it (pointing at
# the disposable dir it creates and wipes); a bare `pytest` falls back to a fresh temp dir.
# luxupt ships on sqlite and connection.py fixes the FILENAME (timelapse.db), so it is the throwaway
# DIRECTORY that carries the test scoping -- which is what the dev-DB guard matches on.
_TMP = Path(tempfile.mkdtemp(prefix="luxupt-test-"))
TEST_DATABASE_URL = os.environ.setdefault(
    "TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_TMP / 'timelapse.db'}"
)


def _guard_not_the_dev_database(url: str) -> None:
    """Refuse to run the suite against anything but a throwaway test database.

    The suite really commits (the durability locks must), so pointing it at the dev database
    mutates real data -- and a FAILING test skips its cleanup, so a stale row can sit in dev
    for a week. The URL must be visibly test-scoped.
    """
    if "test" not in url.rsplit("/", 1)[-1].lower() and "-test" not in url.lower():
        raise RuntimeError(
            f"TEST_DATABASE_URL does not look like a disposable test database: {url!r}. "
            "The suite must NEVER point at the dev database -- `make test` creates the "
            "throwaway one (make test-db-up)."
        )


_guard_not_the_dev_database(TEST_DATABASE_URL)

# app/db/connection.py builds its engine from DATABASE_DIR at import time, so point that at the
# directory the dedicated test URL names -- the test URL stays the single source of truth.
_TEST_DB_PATH = Path(TEST_DATABASE_URL.split("///", 1)[1])
_TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_DIR"] = str(_TEST_DB_PATH.parent)
os.environ.setdefault("IMAGE_OUTPUT_PATH", str(_TMP / "images"))
os.environ.setdefault("VIDEO_OUTPUT_PATH", str(_TMP / "videos"))
os.environ.setdefault("THUMBNAIL_CACHE_PATH", str(_TMP / "thumbnails"))
os.environ.setdefault(
    "WEB_SESSION_SECRET", "test-only-session-secret-not-for-production"
)
os.environ.setdefault("WEB_USERNAME", "testadmin")
os.environ.setdefault("WEB_PASSWORD", "test-password-123")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base  # noqa: E402
from app.db.connection import async_session, engine, seed_singleton_settings  # noqa: E402
from app.web.main import app  # noqa: E402

TEST_USERNAME = os.environ["WEB_USERNAME"]
TEST_PASSWORD = os.environ["WEB_PASSWORD"]


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema() -> AsyncGenerator[None]:
    """Create the sqlite schema once for the session, dispose the engine after."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Seed the singleton settings rows exactly as init_db() does in production. The read path
    # deliberately does NOT create them (a GET must not write -- fw.state_changing_get), so a
    # suite that skipped seeding would exercise a shape production never has.
    await seed_singleton_settings()
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
    maker = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)
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
    # HTTPS base URL: the app is served over HTTPS behind nginx and issues Secure
    # session cookies (fw.secure_cookies), which httpx only sends back over https.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
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
