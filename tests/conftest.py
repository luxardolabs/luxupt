"""Shared test fixtures for luxupt (fastapi-web, sqlite + HTML rendering).

The DB harness is luxlint's canonical one (`luxlint --emit-config conftest`), adapted for SQLite
-- the «EDIT» points plus the two places a file database differs from a Postgres server:

* the schema is built ONCE from the MIGRATION CHAIN (`alembic upgrade head`, never create_all --
  fw.schema_from_migrations) into a template file, and every test gets its own COPY of it. For
  Postgres the canonical harness rejects a TEMPLATE clone (it needs exclusive access to the
  template while cloning); a SQLite file copy has no such coordination and costs microseconds, so
  every test -- durability locks included -- starts from the same migrated schema and leaks
  nothing, whichever owner committed.
* under xdist each worker builds its own template file instead of its own database.

Env is set BEFORE importing anything under ``app``: ``app/db/database.py`` builds its engine from
``DATABASE_DIR`` at import time, so setting it here keeps even the never-used import-time engine
off the dev database. ``WEB_USERNAME``/``WEB_PASSWORD`` enable env-auth so the client can log in
through the REAL login route without seeding DB users.
"""

import asyncio
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

# --- the dedicated test database (luxlint test.db_isolated) ---------------------------
# ONE fleet mechanism: the suite sources a dedicated TEST_DATABASE_URL with a throwaway
# default -- NEVER the app's DATABASE_DIR/DATABASE_URL. `make test` provides it (pointing at
# the disposable dir it creates and wipes); a bare `pytest` falls back to a fresh temp dir.
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

# app/db/database.py builds its engine from DATABASE_DIR at import time, so point that at the
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
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# «EDIT» — the module holding the app's module-level `async_sessionmaker`, and its name. Every
# fixture that gives the test a database points this maker at it, so app code opening its own
# session (`get_db_context()`, the scheduler, durable modules) lands in the SAME database the
# test reads.
import app.db.database as _appdb  # noqa: E402

# «EDIT» — the FastAPI dependency the routes use to get a session
from app.db.database import get_db, seed_singleton_settings  # noqa: E402

# «EDIT» — the app instance (module-level: create_app() registers process-global state such as
# the Prometheus collectors, so it is built once, exactly as in production)
from app.web.main import app  # noqa: E402
from app.web.rate_limit import limiter  # noqa: E402

_APP_MAKER = "async_session"  # «EDIT» the maker's attribute name in that module

# «EDIT» — alembic.ini and the migration scripts, by absolute path (the suite runs from the root)
_APP_DIR = Path(__file__).resolve().parents[1] / "app"

TEST_USERNAME = os.environ["WEB_USERNAME"]
TEST_PASSWORD = os.environ["WEB_PASSWORD"]


def _build_test_schema(url: str) -> None:
    """Build the throwaway DB's schema the SAME WAY dev/prod does — from the MIGRATION CHAIN.

    `Base.metadata.create_all` is banned on an alembic repo (fw.schema_from_migrations): it builds
    from the models, not the chain, so the suite would run on a schema that can silently diverge
    from a real deploy. env.py honours the URL set here (repo.alembic_env_honors_caller_url), so
    this migrates THIS file and never the one DATABASE_DIR would pick.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_APP_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_APP_DIR / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")  # sync API — env.py drives the async URL itself


async def _seed_template(url: str) -> None:
    """Seed the singleton settings rows exactly as init_db() does in production.

    The read path deliberately does NOT create them (a GET must not write --
    fw.state_changing_get), so a suite that skipped seeding would exercise a shape production
    never has. Seeded through the app's own seeder, pointed at the template.
    """
    engine = create_async_engine(url)
    original = getattr(_appdb, _APP_MAKER)
    setattr(_appdb, _APP_MAKER, async_sessionmaker(bind=engine, expire_on_commit=False))
    try:
        await seed_singleton_settings()
    finally:
        setattr(_appdb, _APP_MAKER, original)
        await engine.dispose()


@pytest.fixture(scope="session")
def worker_db_url() -> str:
    """This worker's OWN template database — "gw0"/"gw1"/... under `-n`, the plain URL when serial.

    Read from the ENV VAR xdist sets, not from xdist's `worker_id` fixture, so the suite never
    depends on the plugin being installed.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    if worker == "master":
        return TEST_DATABASE_URL
    base, name = TEST_DATABASE_URL.rsplit("/", 1)
    stem, _, ext = name.partition(".")
    return f"{base}/{stem}_{worker}.{ext or 'db'}"


# The schema is built ONCE per worker, SYNCHRONOUSLY — no event loop is involved, so nothing
# created here can outlive the loop that made it. The engine is deliberately NOT session-scoped.
@pytest.fixture(scope="session")
def _schema(worker_db_url: str) -> Path:
    """Build this worker's TEMPLATE database from the migration chain, once. Returns its path."""
    _guard_not_the_dev_database(worker_db_url)
    path = Path(worker_db_url.split("///", 1)[1])
    path.unlink(missing_ok=True)
    _build_test_schema(worker_db_url)
    asyncio.run(_seed_template(worker_db_url))
    return path


@pytest_asyncio.fixture
async def _engine(_schema: Path, tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """An engine PER TEST on a private COPY of the migrated template, created and disposed in the
    test's OWN event loop — the one engine `db`, `owner_session`, `separate_connection` and
    `durable_db` all draw from.

    It also points the app's module-level maker at this engine for as long as the test runs, so
    app code writing through `get_db_context()` and a test reading through `separate_connection`
    agree on one database. Because the file is this test's own, a durability lock's real commits
    vanish with it — nothing to clean up, no unique key seeded twice.
    """
    db_file = tmp_path / "test.db"
    shutil.copyfile(_schema, db_file)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    original = getattr(_appdb, _APP_MAKER)
    setattr(
        _appdb,
        _APP_MAKER,
        async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False),
    )
    try:
        yield engine
    finally:
        setattr(_appdb, _APP_MAKER, original)
        await engine.dispose()


@pytest_asyncio.fixture
async def db(_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One connection, one outer transaction, and a SAVEPOINT that EVERY session joins.

    `join_transaction_mode="create_savepoint"`: app code calling `commit()` commits a SAVEPOINT
    and the outer transaction survives to be rolled back. The module-level MAKER is swapped too,
    not just the FastAPI dependency, so a session opened by `get_db_context()` joins the same
    transaction instead of escaping the rollback.
    """
    connection = await _engine.connect()
    trans = await connection.begin()
    maker = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
    # Over `_engine`'s real-commit maker for this test; restored to it (not to the app's original).
    previous = getattr(_appdb, _APP_MAKER)
    setattr(_appdb, _APP_MAKER, maker)
    session = maker()
    try:
        yield session
    finally:
        setattr(_appdb, _APP_MAKER, previous)
        await session.close()
        await trans.rollback()  # undo everything this test wrote, through EITHER owner
        await connection.close()


@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Unauthenticated ASGI client whose requests use the SAME rolled-back session.

    Uses ASGITransport, which does not run the app lifespan — so the fetch service / camera
    manager never start. ``app.state.templates`` is wired in ``create_app()`` (not the
    lifespan), so rendering still works. HTTPS base URL: the app is served over HTTPS behind
    nginx and issues Secure session cookies (fw.secure_cookies), which httpx only sends back
    over https.
    """
    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="https://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> None:
    """Start every test with empty rate-limit counters — RESET, never disabled.

    Every test logs in from the same ASGI client address, so without this the login limit
    trips a few tests in and every later `auth_client` is refused. Disabling the limiter would
    hide that the limit refuses; resetting keeps it live for the test that proves it does.
    """
    limiter.reset()


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient) -> AsyncClient:
    """Client logged in via env-auth through the REAL login route; carries the session cookie."""
    resp = await client.post(
        "/login",
        data={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert resp.status_code in (200, 302, 303), (
        f"login failed: {resp.status_code} — {resp.text[:200]}"
    )
    assert client.cookies, "login did not set a session cookie"
    return client


# ── durability fixtures ─────────────────────────────────────────────────────────────────────────
# `db` SWALLOWS a commit() into its outer transaction — invisible to any other connection — so it
# CANNOT prove cross-session durability. A durability lock needs a session that REALLY commits plus
# a SEPARATE connection that sees only committed rows (`luxarch --playbook db-mutations` §7).
@pytest_asyncio.fixture
async def owner_session(_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session that REALLY commits (its own connection, no outer rollback) — the writer."""
    maker = async_sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    async with maker() as s:
        yield s


@pytest_asyncio.fixture
async def separate_connection(_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A DISTINCT connection that sees only COMMITTED rows — the reader in a durability lock."""
    maker = async_sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    async with maker() as s:
        yield s


@pytest_asyncio.fixture
async def durable_db(_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A REAL-commit maker on this test's private database, for locks that need several sessions.

    The owner/reader pair above is one session each; a lock that seeds, acts and then reads back
    from a fresh connection opens them as separate ``maker()`` sessions instead. Mirrors the app
    maker (``expire_on_commit=False``, ``autoflush=False``) so a service behaves exactly as in
    production.
    """
    return async_sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)


# luxarch:route-smoke asserter v19 — DO NOT edit the marker line above (fw.route_smoke_wired finds it).
#
# WHY THIS FILE EXISTS
# A dependency deprecates an API; your code keeps working (warning only); a later pin makes it RAISE;
# `poetry.lock` bumps — and the code is now broken with nothing failing. Not audit (a deprecation is
# not a CVE), not mypy (the attribute still exists; the behaviour changed), not the lock diff. It bit
# WWW twice in six weeks (Starlette TemplateResponse arg order; pydantic `model_fields` instance
# access), both caught by luck. The canonical pytest config already has `filterwarnings = error` — the
# exact mechanism that turns a DeprecationWarning into a failing test one version EARLY. It was inert
# because no test executed the line. The surfaces where deprecations bite hardest (admin pages, error
# handlers, exports) are the least covered. So the fix is not a warning filter — it is: EVERY registered
# route is exercised by the suite, so `filterwarnings = error` actually fires everywhere it matters.
#
# WHAT THIS DOES
# Records every route the suite hits (via a Starlette-level patch — client/app-agnostic) and, at the
# end of the session, fails if any registered HTTP/WebSocket route was never requested. Binary and
# un-gameable: it is the FACT (the route ran), not line-coverage %, not a static guess.
#
# HOW TO WIRE IT (see: luxarch --playbook route-smoke)
#   1) Save this as tests/conftest.py, or paste its body into your existing tests/conftest.py.
#   2) Set APP_IMPORT below to your app factory / instance import.
#   3) Keep `filterwarnings = error` in your pytest config (canonical). Route-smoke + that pairing is
#      what catches the deprecation BEFORE the pin that makes it fatal — either alone is insufficient.
#   4) Cover the hard routes for real: log in through the REAL login route (never forge a cookie or
#      override the auth dependency); assert a route was REQUESTED, not a response shape (fragment
#      endpoints legitimately return bare HTML); reset the rate-limit counter, don't disable the limiter.
#      For the canonical PATTERNS as a fill-in scaffold, run `luxarch --emit route-smoke-example` — the
#      technique is versioned in the image so you copy it, never a drifting other-repo's tests.
#   5) Exempt only what genuinely has no test surface (health/metrics) — with a comment saying why.
#
# NOTE FOR ADOPTERS AND FOR WHOEVER EDITS THIS ASSET NEXT: this block carries NO module-level imports,
# deliberately. It is pasted BELOW existing code in an existing tests/conftest.py, where
# `from __future__ import …` is a SyntaxError (it must be the first statement) and any other import is
# ruff E402 — and luxlint's emitted-asset exemption drops T201/T203/PLC0415, NOT E402. So an import
# here leaves an adopter choosing between editing a DO-NOT-EDIT block and carrying a permanent red.
# v9 had none; v11 added two and cost www exactly that (WWWLUXARDO-163). Import inside a function if
# you ever need one — PLC0415's exemption already covers that.

# --- EDIT THIS: how your app object is imported (the thing that owns the route table). ---
APP_IMPORT = "app.web.main:app"  # "package.module:attribute"

# --- EDIT THIS: routes a test DELIBERATELY drives to a 5xx (error-handler coverage). Each needs a
# why. Anything not listed here that returns 5xx during the suite is a real bug and fails the run. ---
EXPECT_5XX: frozenset[str] = frozenset(
    {
        # The readiness probe answers 503 under the suite BY DESIGN: the client is an
        # ASGITransport, which does not run the app lifespan, so the fetch service and
        # camera manager never start and the probe correctly reports "not ready". Starting
        # the lifespan in tests would reach the real Protect API. tests/integration/
        # test_pages_render.py::test_health_responds asserts 200-or-503 for this reason.
        "/health",
    }
)

# --- EDIT THIS: GET routes that ALWAYS redirect by design, and that the suite therefore only ever
# sees as a 3xx. Each needs a why. Ships EMPTY. LUXMOMENTU-76: a suite logged its session-scoped
# client out in one test file, and because `test_route_smoke.py` sorts after it, ALL 46 authenticated
# routes were requested logged out. Each returned 307 to the login page, each satisfied
# `status_code < 500`, and the suite proved the login redirect worked and nothing else. A genuine 500
# — a template reading a context key the handler never set — passed 417 tests.
#
# The bar was "the route RAN", and an auth bounce means the HANDLER never ran: only the middleware
# did. So a GET route the suite never saw produce a non-3xx has not been covered, whatever `_HIT`
# says. Scoped to GET because a POST/PUT/DELETE that always redirects is post-redirect-get and
# correct — excluding those by STRUCTURE rather than by allowlist is what keeps this list short.
#
# Note the shape of the escape: the LUXMOMENTU case would have needed 46 entries here. One line is a
# considered exception; forty-six is a confession.
EXPECT_3XX: frozenset[str] = frozenset(
    {
        "/",  # a bare RedirectResponse to /cameras -- the handler IS the redirect
        "/dashboard",  # the retired dashboard URL, kept as a redirect to /cameras for old links
        "/logout",  # clears the session and redirects to /login; nothing renders by design
    }
)

# --- EDIT THIS: routes with no meaningful test surface. Keep it short; each needs a why. ---
# Ships EMPTY, with the common cases commented — like EXPECT_5XX above. It used to ship `/health`
# and `/metrics` LIVE as "examples", and that is what caused LUXMOMENTU-61: a repo inherited an
# exemption for `/metrics`, the route was never registered (`get_metrics()` had no caller, so the
# Prometheus registry could never be scraped), and the pre-written comment — "prometheus scrape — no
# behaviour to assert" — made an absence that nobody had reviewed read as a decision somebody had.
# An exemption you did not write is the most expensive kind: it survives onboarding and a full
# burn-down because it looks considered. Uncomment only what this app actually serves.
EXEMPT: frozenset[str] = frozenset(
    {
        "/health/live",  # liveness probe — no behaviour to assert
        "/health/ready",  # readiness probe — no behaviour to assert
        "/metrics",  # prometheus scrape — no behaviour to assert
    }
)

# ── recording (do not edit below) ──────────────────────────────────────────────────────────────
_HIT: set[int] = (
    set()
)  # ids of route objects that were exercised (see _patch_route_class)
_OUTCOMES = {"passed": 0, "failed": 0, "skipped": 0}
# route id -> the 5xx statuses the suite saw from it. BOUTIQUE-549: --playbook route-smoke line 84
# claims route-smoke "catches ordinary logic bugs that 500 on first request", but nothing recorded
# status, so that sentence was only true when a test author happened to assert on it. Four prod
# 500s shipped while this asserter reported PASS. Recording status is NOT a response-SHAPE
# assertion — a bare-HTML fragment still passes; only a 5xx fails.
_SERVER_ERRORS: dict[int, set[int]] = {}
# route id -> the (status, location-path) pairs the suite saw, and the set of routes that produced at
# least one NON-redirect response. Together these answer "did the handler ever actually run?", which
# `_HIT` cannot: a 307 from auth middleware records a hit for a handler that never executed
# (LUXMOMENTU-76). A 4xx counts as rendered — an explicit refusal is the route answering, where a
# silent bounce to a login page is not.
_REDIRECTS: dict[int, set[tuple[int, str]]] = {}
_RENDERED: set[int] = set()
# Under `pytest -n` (xdist) every worker is its own process with its own recorder, and the CONTROLLER,
# where pytest_sessionfinish certifies, runs no test at all: it saw 0 requests and refused a green run
# (OPENCLAIM-365). Route ids differ per process, so each worker translates its ids to (host, method,
# path) before handing them back, and the controller maps them onto its own ids.
_WORKER_PAYLOADS: list[dict[str, list[object]]] = []


def pytest_runtest_logreport(report) -> None:  # type: ignore[no-untyped-def]
    # Count outcomes so the 0-hits net can tell "the request suite SKIPPED (e.g. a DB-less run)" from
    # "tests ran but the recorder saw nothing (real misconfig)" — WWWLUXARDO-70. A real skip is decided
    # at SETUP. CRITICAL: an xfail RAN — pytest reports it at the CALL phase with outcome=="skipped" and
    # `wasxfail` set, so it must NOT be counted as a skip, or a single xfail (the fleet's own
    # known-broken idiom) would disarm net #2 and route-smoke would certify nothing (LUXSTATS).
    if report.when == "setup" and report.outcome == "skipped":
        _OUTCOMES["skipped"] += 1
    elif report.when == "call":
        outcome = report.outcome
        if outcome == "skipped" and getattr(report, "wasxfail", False):
            outcome = "xfailed"  # an xfail executed; it is not a skip
        _OUTCOMES[outcome] = _OUTCOMES.get(outcome, 0) + 1


def _patch_route_class(cls, match_full) -> None:  # type: ignore[no-untyped-def]
    # Record the route OBJECT'S IDENTITY, not (method, path). The recorder only ever sees `self`, which
    # knows its UNPREFIXED path, while the mount prefix lives on the parent wrapper — so a string key
    # could never agree between recording and enumeration (they'd both have to reconstruct the full
    # path, and the recorder can't). Keying on `id(route)` makes the two sides agree BY CONSTRUCTION:
    # enumeration walks the same objects and records their ids. (LUXSTATS-67: proven end-to-end.)
    # Record on BOTH matches() (fires on a FULL match even if a dep later 403s — import-time deprecations
    # still caught) and handle() (body dispatch). Idempotent per class; `_HIT` is a set so a super()
    # double-record is harmless.
    if getattr(cls.handle, "_luxarch_route_smoke", False):
        return
    _orig_matches = cls.matches

    def matches(self, scope):  # type: ignore[no-untyped-def]
        result = _orig_matches(self, scope)
        try:
            if result[0] == match_full:
                _HIT.add(id(self))
        except Exception:
            pass
        return result

    matches._luxarch_route_smoke = True  # type: ignore[attr-defined]
    cls.matches = matches

    _orig_handle = cls.handle

    async def handle(self, scope, receive, send):  # type: ignore[no-untyped-def]
        _HIT.add(id(self))

        async def _send(message):  # type: ignore[no-untyped-def]
            # `http.response.start` carries the status. Wrapped rather than inspected after the fact
            # so it works for any client and for streaming responses alike.
            try:
                if message.get("type") == "http.response.start":
                    status = int(message.get("status", 0))
                    if status >= 500:
                        _SERVER_ERRORS.setdefault(id(self), set()).add(status)
                    if 300 <= status < 400:
                        loc = ""
                        for k, v in message.get("headers") or ():
                            if k.lower() == b"location":
                                loc = v.decode("latin-1", "replace")
                                break
                        # Query string dropped: `?next=/admin/users` differs per route and would
                        # hide that a dozen routes all bounce to the SAME page.
                        _REDIRECTS.setdefault(id(self), set()).add(
                            (status, loc.split("?")[0])
                        )
                    else:
                        _RENDERED.add(id(self))
            except Exception:
                pass
            return await send(message)

        return await _orig_handle(self, scope, receive, _send)

    handle._luxarch_route_smoke = True  # type: ignore[attr-defined]
    cls.handle = handle


def _install_recorder() -> None:
    # CRITICAL: FastAPI's APIRoute OVERRIDES both handle() and matches() (subclass wins), and FastAPI
    # dispatches by calling `original_route.handle(...)` on the APIRoute directly — so patching only
    # the Starlette parent Route recorded ZERO hits (v1 patched handle, v2 added matches; both missed).
    # Patch APIRoute FIRST, then the Starlette Route (WebSocketRoute etc. still go through Route). The
    # `Match.FULL` enum is shared. (WWWLUXARDO-67: root-caused + fix confirmed live by the www agent.)
    import starlette.routing as _r

    _patch_route_class(_r.Route, _r.Match.FULL)
    try:
        import fastapi.routing as _fr

        _patch_route_class(_fr.APIRoute, _r.Match.FULL)
    except (
        Exception
    ):  # no FastAPI (shouldn't happen for a fastapi-web repo) — Route patch stands
        pass


_install_recorder()


def _registered_route_entries() -> list[tuple[str, str, str, int]]:
    # (HOST, method, FULL prefixed path, id(route)). The id is what matching keys on (agrees with the
    # recorder by construction). The HOST is carried down so shadow-detection keys on (host, method,
    # path): a Host()-composed app reaches the SAME route object once per hostname, and different
    # sub-apps legitimately share `/`, `/docs`, … — neither is a shadow, because Starlette matches
    # exactly ONE host per request (BOUTIQUE-410 v8 false-positive). The prefix is NOT on the route —
    # it's on the wrapper: `_IncludedRouter.include_context.prefix` (empty on `.original_router.prefix`)
    # and `Mount.path`. (LUXSTATS-67.)
    mod, _, attr = APP_IMPORT.partition(":")
    import importlib

    app = getattr(importlib.import_module(mod), attr or "app")
    entries: list[tuple[str, str, str, int]] = []

    def _walk(routes, prefix="", host="") -> None:  # type: ignore[no-untyped-def]
        for route in routes:
            # Starlette >=1.x wraps include_router() results instead of flattening — the real routes hang
            # off `.original_router.routes`, and the include prefix lives on the wrapper's include_context.
            inner = getattr(route, "original_router", None)
            if inner is not None:
                ictx = getattr(route, "include_context", None)
                _walk(inner.routes, prefix + (getattr(ictx, "prefix", "") or ""), host)
                continue
            sub = getattr(route, "routes", None)
            kind = type(route).__name__
            if kind == "Mount":  # a Mount (sub-app) carries its prefix on `.path`
                mpath = prefix + (getattr(route, "path", "") or "")
                # Record the mount's OWN path with a sentinel method. A Mount has no `.methods` and a
                # bare ASGI sub-app (`app.mount("/metrics", make_asgi_app())`) has no `.routes`
                # either, so it used to vanish from the walk entirely — invisible to BOTH halves:
                # never reportable as uncovered, and, once v13 added the inverse check, never
                # exemptable either, because the exemption read as stale. The one surface this
                # asserter cannot check was also the one it refused to let you document, and the
                # message said "describing a route that does not exist" about a route that was
                # serving 200s (LUXTASTE-334).
                #
                # The sentinel keeps it out of the COVERAGE requirement — a third-party ASGI app's
                # internals are not this asserter's business — while making it visible to the stale
                # check.
                entries.append((host, "MOUNT", mpath, id(route)))
                if sub:
                    _walk(sub, mpath, host)
                continue
            if (
                sub is not None and kind == "Host"
            ):  # discriminates on HOSTNAME → no path prefix, new host
                _walk(
                    sub,
                    prefix,
                    getattr(route, "host", "") or getattr(route, "path", "") or host,
                )
                continue
            path = prefix + getattr(route, "path", "")
            methods = getattr(route, "methods", None)
            if methods:  # APIRoute / Route
                for m in set(methods) - {"HEAD", "OPTIONS"}:
                    entries.append((host, m, path, id(route)))
            elif type(route).__name__ == "WebSocketRoute":
                entries.append(
                    (
                        host,
                        "WS",
                        prefix
                        + str(
                            getattr(route, "path_format", None)
                            or getattr(route, "path", "")
                            or ""
                        ),
                        id(route),
                    )
                )

    _walk(app.routes)
    return entries


# Below this many registered routes, an app with included routers is almost certainly mis-walked
# (the flat-walk bug), not genuinely tiny — fail loud rather than "certify" a handful.
_MIN_PLAUSIBLE_ROUTES = 5


def _session_is_narrowed(session) -> bool:  # type: ignore[no-untyped-def]
    """True when this run deliberately selected a SUBSET of the suite.

    LUXTASTE-307: net #2 exists to catch a blind RECORDER on a run where request tests ran and
    recorded nothing. A focused run (`pytest tests/unit/x.py`, `-k`, a node id) selects no request
    tests at all, so zero hits is the correct outcome — not evidence of misconfiguration. The only
    stand-down signal was the skip count, and a narrowed selection has 0 skips, so every focused
    unit-test run "failed". That is the everyday TDD loop, and a red exit there trains people to
    ignore the exit code, which blunts the loud-fail nets that actually matter.

    Certification requires the WHOLE configured suite; anything narrower says so and stands down."""
    # If narrowing cannot be determined, answer NOT narrowed so every loud-fail net still fires. The
    # opposite default would let an unrecognised session shape stand the asserter down silently —
    # a hollow green, which is the failure this whole file exists to prevent.
    opt = getattr(getattr(session, "config", None), "option", None)
    if opt is None:
        return False
    if getattr(opt, "keyword", "") or getattr(opt, "markexpr", ""):
        return True  # -k / -m
    if getattr(session, "deselected", 0):
        return True
    try:
        testpaths = list(session.config.getini("testpaths") or [])
    except Exception:
        testpaths = []
    try:
        args = [a for a in (session.config.args or []) if not a.startswith("-")]
    except Exception:
        return False
    if not args:
        return False  # bare `pytest` with testpaths from config — the full suite
    # Explicit args that are not exactly the configured testpaths = a deliberate subset. A node id
    # (`file::test`) is always narrower than a path.
    if any("::" in a for a in args):
        return True
    import os.path

    # LUXSTATS-111: the string comparison below reported EVERY run as narrowed under the fleet's own
    # canonical config, so route-smoke certified nothing on any repo using it — while
    # fw.route_smoke_wired stayed green and the stand-down message read like normal operation ("run
    # the full suite to certify", when the full suite is what ran). The reporter's probe on a real
    # `make test`:
    #
    #     rootdir /   inifile /pytest.ini   testpaths ['tests']   args ['/w']   deselected 0
    #
    # `luxlint --emit-config pytest` ships `testpaths = tests`, and the fleet pattern mounts that
    # file read-only and runs `pytest -c /pytest.ini` with the repo at the work dir. rootdir becomes
    # `/`, so `testpaths` cannot resolve against it and pytest falls back to collecting from the cwd,
    # setting args to the work dir. Collection is complete; only the comparison breaks. Two guards
    # disagreeing about the same prescribed setup, with the asserter the one that lost.
    #
    # So: pytest's own fallback is not a deliberate subset.
    root = os.path.normpath(str(getattr(session.config, "rootpath", "") or ""))
    here = os.path.normpath(os.getcwd())
    if len(args) == 1 and os.path.normpath(args[0]).rstrip("/") in {root, here}:
        return False
    # v16 also had an "ini file outside the rootdir -> not narrowed" leg here. It is GONE, because it
    # was redundant AND harmful (LUXPM-233). Redundant: the rootdir/cwd check above already handles
    # every case LUXSTATS-111 reported — verified against their probe. Harmful: it returned
    # not-narrowed OUTRIGHT, discarding the args, so `pytest -c /cfg/pytest.ini --rootdir=/app
    # tests/test_tool_gate.py` read as the full suite, recorded zero route hits and the zero-hits net
    # REFUSED. That is LUXTASTE-307's failure — the everyday focused run goes red — coming back
    # through a new leg. The comment beside it said it should only skip the testpaths COMPARISON; the
    # code switched off the whole determination. Correct intent, broader implementation.
    #
    # Deleted rather than reordered: with the rootdir/cwd check present it has no remaining job, and a
    # leg that does nothing is one someone later has to work out the purpose of.
    #
    # Args are compared BOTH as written and relative to the cwd/rootdir, so `pytest /app/tests` with
    # `testpaths = tests` is recognised as the full suite rather than standing the asserter down —
    # a stand-down is a hollow green, and the absolute spelling of a configured testpath is not a
    # deliberate subset.
    want = {os.path.normpath(t).rstrip("/") for t in testpaths}
    got: set[str] = set()
    for a in args:
        n = os.path.normpath(a).rstrip("/")
        got.add(n)
        for base in (here, root):
            if base and n.startswith(base.rstrip("/") + os.sep):
                got.add(n[len(base.rstrip("/")) + 1 :])
    if not want:
        # No testpaths to compare against: an explicit FILE is the only thing that still reads as a
        # deliberate subset. A directory arg with no configured testpaths could be the whole suite.
        return any(a.endswith(".py") for a in args)
    # COVERAGE, not intersection. `pytest tests` under `testpaths = ["tests", "integration"]` runs half
    # the suite, and an intersection test would call that the full one — a stand-down, which is a
    # hollow green. Every configured testpath must be named for this to be the whole suite.
    return not want <= got


def _by_rid(
    entries: list[tuple[str, str, str, int]],
) -> dict[int, list[tuple[str, str, str]]]:
    out: dict[int, list[tuple[str, str, str]]] = {}
    for h, m, p, rid in entries:
        out.setdefault(rid, []).append((h, m, p))
    return out


def _worker_payload(
    entries: list[tuple[str, str, str, int]],
) -> dict[str, list[object]]:
    """This worker's recordings, keyed by (host, method, path) so another process can read them."""
    by_rid = _by_rid(entries)

    def keyed(rids: set[int]) -> list[tuple[str, str, str]]:
        return sorted({k for r in rids for k in by_rid.get(r, ())})

    return {
        "hit": [list(k) for k in keyed(_HIT)],
        "rendered": [list(k) for k in keyed(_RENDERED)],
        "server_errors": [
            [list(k), sorted(v)]
            for r, v in _SERVER_ERRORS.items()
            for k in by_rid.get(r, ())
        ],
        "redirects": [
            [list(k), [list(x) for x in sorted(v)]]
            for r, v in _REDIRECTS.items()
            for k in by_rid.get(r, ())
        ],
    }


def _route_key(o: object) -> tuple[str, ...] | None:
    return tuple(str(x) for x in o) if isinstance(o, list) else None


def _merge_worker_payloads(entries: list[tuple[str, str, str, int]]) -> None:
    rid_by_key: dict[tuple[str, ...], int] = {
        (h, m, p): rid for h, m, p, rid in entries
    }

    def rid_of(o: object) -> int | None:
        key = _route_key(o)
        return rid_by_key.get(key) if key is not None else None

    for payload in _WORKER_PAYLOADS:
        for k in payload.get("hit", []):
            if (rid := rid_of(k)) is not None:
                _HIT.add(rid)
        for k in payload.get("rendered", []):
            if (rid := rid_of(k)) is not None:
                _RENDERED.add(rid)
        for item in payload.get("server_errors", []):
            if (
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[1], list)
                and (rid := rid_of(item[0])) is not None
            ):
                _SERVER_ERRORS.setdefault(rid, set()).update(
                    int(str(st)) for st in item[1]
                )
        for item in payload.get("redirects", []):
            if (
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[1], list)
                and (rid := rid_of(item[0])) is not None
            ):
                _REDIRECTS.setdefault(rid, set()).update(
                    (int(str(x[0])), str(x[1]))
                    for x in item[1]
                    if isinstance(x, list) and len(x) == 2
                )


def _xdist_merge_hook() -> object:
    # The controller collects each worker's payload as the worker goes down. `pytest_testnodedown` is
    # xdist's own hook, so it is marked `optionalhook=True` (inert without xdist), and the decorator
    # needs `pytest`, which this block may only import inside a function (see the NOTE above).
    # v19: built here and bound below rather than registered from a `pytest_configure`. v18 defined
    # `pytest_configure`, and this block is pasted into the repo's own conftest, where Python keeps
    # only the LAST def of a name: it silently replaced the repo's hook (LUXSWIRL-245: three
    # dynamically registered markers vanished, 12 files failed collection under --strict-markers).
    # This block defines no hook name a repo's conftest would also define.
    import pytest

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(node: object, error: object) -> None:
        payload = getattr(node, "workeroutput", {}).get("luxarch_route_smoke")
        if payload:
            _WORKER_PAYLOADS.append(payload)

    return pytest_testnodedown


pytest_testnodedown = _xdist_merge_hook()


def pytest_sessionfinish(session, exitstatus) -> None:  # type: ignore[no-untyped-def]
    # Only CERTIFY on a green run — a red suite's route list is untrustworthy (a failed test may have
    # aborted before its route ran). But say so OUT LOUD: a silent skip lets coverage lapse invisibly
    # while the suite is red (LUXTASTE route-smoke escalation). Non-enforcing by design; just visible.
    if exitstatus not in (0, None):
        print(
            "\nluxarch route-smoke: INERT — suite exitstatus="
            f"{exitstatus}, route certification SKIPPED. NO route is certified until the failing "
            "test(s) are fixed. See luxarch --playbook route-smoke."
        )
        return
    try:
        entries = _registered_route_entries()
    except (
        Exception
    ) as exc:  # app import misconfigured — make it loud, not silently green
        raise SystemExit(
            f"luxarch route-smoke: could not import the app via APP_IMPORT={APP_IMPORT!r} "
            f"({exc}). Set it to your app factory/instance. See luxarch --playbook route-smoke."
        ) from exc
    workeroutput = getattr(getattr(session, "config", None), "workeroutput", None)
    if workeroutput is not None:
        # An xdist WORKER: hand the recordings to the controller and certify nothing here. Each worker
        # saw only its share of the suite, so a per-worker verdict would be wrong in both directions.
        workeroutput["luxarch_route_smoke"] = _worker_payload(entries)
        return
    _merge_worker_payloads(entries)
    # Loud-fail net #3: a duplicate (HOST, method, path) — from DISTINCT route objects — means the app
    # registered the same method+path twice under one host, so one handler shadows the other (the
    # shadowed one is dead/unreachable). Keyed on HOST because a Host()-composed app reaches the same
    # route under N hostnames and different sub-apps share `/`, `/docs`, … — those are not shadows
    # (Starlette matches exactly one host per request), so keying on (method, path) alone false-flagged
    # thousands (BOUTIQUE-410 v8). A collision is real only when two DIFFERENT route ids share it.
    seen: dict[tuple[str, str, str], int] = {}
    shadowed: set[tuple[str, str, str]] = set()
    for h, m, p, rid in entries:
        k = (h, m, p)
        if k in seen and seen[k] != rid:
            shadowed.add(k)
        else:
            seen.setdefault(k, rid)
    if shadowed:
        dupes = sorted(f"{m} {p}" + (f" @{h}" if h else "") for (h, m, p) in shadowed)
        raise SystemExit(
            f"luxarch route-smoke: {len(shadowed)} (host, method, path) registered by more than one "
            f"handler — one SHADOWS the other (the shadowed one is dead/unreachable). Remove the dead "
            f"handler. Duplicates: {dupes}. See --playbook route-smoke."
        )
    # Loud-fail net #1: implausibly few (INCLUDING ZERO) routes → the enumerator is mis-walking, NOT a
    # 2-route app. ZERO is the most certain "the walk is broken" signal, so it must fail LOUDEST — the
    # old `0 < len(entries)` guard excluded zero, so a walk-to-nothing certified GREEN silently, the exact
    # hollow green this net exists to prevent (BOUTIQUE-410).
    if not entries:
        raise SystemExit(
            "luxarch route-smoke: enumerated 0 routes — the walk found NOTHING, so this run certifies "
            "nothing (a hollow green). Either APP_IMPORT is wrong, or the app composes its routes in a "
            "container the walker doesn't descend (a Host()/Mount sub-app, an unusual router wrapper). "
            "Refusing to certify. Re-emit the asserter (luxarch --emit route-smoke) and check APP_IMPORT. "
            "See --playbook route-smoke."
        )
    if len(entries) < _MIN_PLAUSIBLE_ROUTES:
        raise SystemExit(
            f"luxarch route-smoke: only {len(entries)} route(s) enumerated — implausibly few for an "
            "app with included routers, so the asserter is almost certainly mis-walking the route table "
            "(Starlette version?), NOT genuinely tiny. Refusing to certify — a low count would be a "
            "hollow green. Update the asserter (luxarch --emit route-smoke). See --playbook route-smoke."
        )
    # Loud-fail net #2: routes exist but NOTHING was recorded. Two cases, cleanly separable by the skip
    # count (WWWLUXARDO-70): if the request suite SKIPPED (a DB-less `make test` → 0 hits / many skipped),
    # route-smoke simply wasn't exercised this run — that's NOT a misconfiguration; stand down (the
    # DB-full run enforces coverage). Only when tests actually RAN and still recorded nothing is the
    # recorder blind (wrong app / bypassing TestClient) — THAT is the misconfig to fail on.
    if entries and not _HIT:
        if _session_is_narrowed(session):
            print(
                "luxarch route-smoke: not evaluated — this run selected a SUBSET of the suite, which "
                "need not contain any route test. Certification requires the full suite."
            )
            return
        if _OUTCOMES["skipped"]:
            print(
                f"luxarch route-smoke: not evaluated — 0 requests observed but {_OUTCOMES['skipped']} "
                "test(s) skipped, so the request suite didn't run this pass (DB-less?). The DB-full run "
                "enforces coverage."
            )
            return
        raise SystemExit(
            "luxarch route-smoke: 0 requests were observed across the whole suite while "
            f"{len(entries)} routes are registered and nothing skipped. TWO causes look identical from "
            "zero hits: (a) the recorder isn't observing — wrong APP_IMPORT, or a TestClient that "
            "bypasses the app (e.g. Starlette 1.x needs httpx2 installed, or the test network has no DNS "
            "for db/redis so every request dies before routing); or (b) the suite has NO route tests "
            "yet. Make ONE real request — a single public-page GET — to disambiguate: if it records, "
            "it's (b), you have real coverage gaps to fill (KEEP that probe, it's load-bearing — remove "
            "the last route test and this refusal returns). If it still doesn't record, it's (a), fix "
            "the wiring. Under `pytest -n` the workers' recordings are merged here (asserter v18+); an "
            "OLDER asserter under xdist shows exactly this message on a healthy suite, so re-emit it "
            "(luxarch --emit route-smoke) before hunting (a). Refusing to certify on zero requests "
            "either way. See --playbook route-smoke."
        )
    # Loud-fail net #4: a route the suite REQUESTED returned 5xx. BOUTIQUE-549 — `--playbook
    # route-smoke` line 84 claims route-smoke "catches ordinary logic bugs that 500 on first request",
    # but nothing looked at status, so the claim held only where a test author happened to assert on
    # it. A test that requests POST /vendors and gets a 500 satisfied the old asserter completely;
    # four such 500s shipped to production while this file reported PASS.
    #
    # This is NOT the response-SHAPE assertion the playbook warns against — a fragment endpoint
    # returning bare HTML still passes. Only a 5xx fails, and EXPECT_5XX exempts a route a test drives
    # to an error on purpose.
    if _SERVER_ERRORS:
        by_rid = {rid: (m, p_) for (_h, m, p_, rid) in entries}
        bad = sorted(
            f"{by_rid[rid][0]:4} {by_rid[rid][1]}  -> {sorted(codes)}"
            for rid, codes in _SERVER_ERRORS.items()
            if rid in by_rid and by_rid[rid][1] not in EXPECT_5XX
        )
        if bad:
            session.exitstatus = 1
            raise SystemExit(
                f"luxarch route-smoke: {len(bad)} route(s) returned 5xx to the suite. The request was "
                "recorded, so route coverage looks satisfied — but the route is BROKEN. Fix it, or if "
                "a test drives it to an error deliberately, add the path to EXPECT_5XX with a reason:"
                "\n  " + "\n  ".join(bad) + "\nSee luxarch --playbook route-smoke."
            )
    # Loud-fail net #5: every response a GET route gave the suite was a REDIRECT (LUXMOMENTU-76).
    # `_HIT` records that the route MATCHED, which an auth bounce satisfies — the middleware ran and
    # the handler did not. A reporting repo logged its session-scoped client out in one test file, and
    # because the smoke file sorts alphabetically after it, all 46 authenticated routes were requested
    # logged out: 46 x 307-to-login, every assertion satisfied, a real 500 sailing through 417 tests.
    #
    # Scoped to GET by STRUCTURE: a POST/PUT/DELETE that always redirects is post-redirect-get and
    # correct, so excluding it needs no allowlist entry and cannot rot.
    methods_by_rid: dict[int, set[str]] = {}
    for _h, m, _p, rid in entries:
        methods_by_rid.setdefault(rid, set()).add(m)
    bounced = {
        rid: (p_, sorted(_REDIRECTS[rid]))
        for (_h, _m, p_, rid) in entries
        if rid in _REDIRECTS
        and rid not in _RENDERED
        and "GET" in methods_by_rid.get(rid, set())
        and p_ not in EXPECT_3XX
        and p_ not in EXEMPT
    }
    if bounced:
        # Group by target. One route redirecting is business logic; a dozen routes redirecting to the
        # SAME page is a gate, and naming that page turns a list of failures into one diagnosis.
        targets: dict[str, int] = {}
        for _p, obs in bounced.values():
            for _st, loc in obs:
                targets[loc] = targets.get(loc, 0) + 1
        lines = sorted(
            f"GET  {p_}  -> "
            + ", ".join(f"{st} -> {loc or '(no Location)'}" for st, loc in obs)
            for p_, obs in bounced.values()
        )
        shared = max(targets.items(), key=lambda kv: kv[1]) if targets else ("", 0)
        hint = ""
        if shared[1] >= 3:
            hint = (
                f"\n{shared[1]} of them redirect to the SAME target ({shared[0]}). That is the shape "
                "of an AUTHENTICATION bounce, not of business logic — the usual cause is a shared, "
                "session-scoped client that some earlier test logged out and never signed back in. "
                "Check whether a logout test leaves the fixture authenticated for the files that sort "
                "after it."
            )
        session.exitstatus = 1
        raise SystemExit(
            f"luxarch route-smoke: {len(bounced)} GET route(s) NEVER returned a non-redirect to the "
            "suite. The request was recorded, so coverage looks satisfied — but a 3xx means the "
            "HANDLER did not run, so nothing in it was exercised and a deprecation or a broken "
            "template inside it ships latent. Request them in a state that renders, or list a route "
            "that redirects BY DESIGN in EXPECT_3XX with a reason:\n  "
            + "\n  ".join(lines)
            + hint
            + "\nSee luxarch --playbook route-smoke."
        )
    # Covered iff THAT route object was exercised (id match) — the two sides agree by construction. One
    # route object reached under N hostnames is ONE route to cover, so collapse on route identity (rid)
    # — else a Host()-composed app reports the same uncovered route once per host (BOUTIQUE-410 v8).
    # The INVERSE check, and it is the more dangerous direction. Subtracting EXEMPT from the
    # registered routes trusted the list without ever asking whether the exempted route EXISTS. A
    # repo carried `/metrics` in EXEMPT from onboarding, commented "prometheus scrape — no behaviour
    # to assert", and the route was never registered: `get_metrics()` had no caller and the registry
    # could never be scraped. That survived onboarding, a full guard burn-down, and
    # `fw.route_smoke_wired` reporting PASS throughout, and was found only by reading /metrics by
    # hand. The exemption did not merely fail to catch the gap — it is WHY nothing looked wrong: a
    # missing route with no exemption reads as untested or absent, while a missing route WITH one
    # reads as a deliberate, reviewed decision (LUXMOMENTU-61).
    #
    # Deliberately NOT skipped on a narrowed run: a subset selection still REGISTERS every route, so
    # unlike `missing` this count is meaningful even from one test.
    # Mounts included on purpose: a mount IS a registered surface, so exempting one is legitimate.
    registered_paths = {p for (_h, _m, p, _rid) in entries}
    # `app.mount("/metrics", ...)` serves at `/metrics/`; a redirect from `/metrics` is the normal
    # shape, so accept either spelling in EXEMPT rather than making the trailing slash load-bearing.
    registered_paths |= {p.rstrip("/") for p in registered_paths}
    registered_paths |= {p + "/" for p in registered_paths if not p.endswith("/")}
    # Every path list gets this, not just EXEMPT. LUXMOMENTU-61 was about EXEMPT, but EXPECT_5XX has
    # carried the identical hazard since it was added and nothing checked it: a path naming no
    # registered route reads as a reviewed decision while actually hiding the route's absence. Fixing
    # one instance of a class and leaving its siblings is how the same bug gets reported twice.
    stale = sorted(
        f"{name}: {path}"
        for name, paths in (
            ("EXEMPT", EXEMPT),
            ("EXPECT_5XX", EXPECT_5XX),
            ("EXPECT_3XX", EXPECT_3XX),
        )
        for path in paths - registered_paths
    )
    if stale:
        session.exitstatus = 1
        raise SystemExit(
            "luxarch route-smoke: "
            f"{len(stale)} exemption path(s) match no registered route — the entry is describing a "
            "route that does not exist, which HIDES its absence rather than documenting a decision. "
            "Remove the entry, or register the route:\n  "
            + "\n  ".join(stale)
            + "\nSee luxarch --playbook route-smoke."
        )
    missing_by_rid = {
        rid: f"{m:4} {p}"
        for (h, m, p, rid) in entries
        if rid not in _HIT and p not in EXEMPT and m != "MOUNT"
    }
    missing = sorted(missing_by_rid.values())
    if missing and _session_is_narrowed(session):
        print(
            f"luxarch route-smoke: not evaluated — {len(missing)} route(s) unrequested, but this run "
            "selected a SUBSET of the suite so that count is meaningless. Run the full suite to certify."
        )
        return
    if missing:
        session.exitstatus = 1
        raise SystemExit(
            "luxarch route-smoke: "
            f"{len(missing)} registered route(s) were NEVER requested by the suite — a deprecation "
            "in any of them ships latent (fine on the running pin, fatal on the next). Add a test "
            "that requests each, or EXEMPT it with a reason:\n  "
            + "\n  ".join(missing)
            + "\nSee luxarch --playbook route-smoke."
        )
