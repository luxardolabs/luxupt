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


# luxarch:route-smoke asserter v12 — DO NOT edit the marker line above (fw.route_smoke_wired finds it).
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

# --- EDIT THIS: routes with no meaningful test surface. Keep it short; each needs a why. ---
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
            if (
                sub is not None and kind == "Mount"
            ):  # a Mount (sub-app) carries its prefix on `.path`
                _walk(sub, prefix + (getattr(route, "path", "") or ""), host)
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

    norm = {os.path.normpath(a).rstrip("/") for a in args}
    return norm != {os.path.normpath(t).rstrip("/") for t in testpaths}


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
            "the wiring. Refusing to certify on zero requests either way. See --playbook route-smoke."
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
    # Covered iff THAT route object was exercised (id match) — the two sides agree by construction. One
    # route object reached under N hostnames is ONE route to cover, so collapse on route identity (rid)
    # — else a Host()-composed app reports the same uncovered route once per host (BOUTIQUE-410 v8).
    missing_by_rid = {
        rid: f"{m:4} {p}"
        for (h, m, p, rid) in entries
        if rid not in _HIT and p not in EXEMPT
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
