# luxarch:test-db-bound asserter v5 — DO NOT edit the marker line above (repo.makefile_test_db_harness finds it).
#
# WHY THIS FILE EXISTS
# A suite whose only database is a SQLite file must run on a file it makes for the run, never the dev
# one. Reading that from the conftest's code failed every round: an override cleared by a later
# fixture, the override map reassigned or popped, the URL rewritten after the engine was built, an
# override behind a branch that never runs, an app that opens its own SessionLocal in its lifespan, a
# report engine built lazily or in a thread. Each read green and wrote to the dev database. So the
# suite is asked, while it runs.
#
# WHAT THIS DOES
# - From the moment this conftest is imported, EVERY connection any SQLAlchemy engine in the process
#   opens (`Engine` / `AsyncEngine`, built at import, lazily, in a thread, or built and dropped; and
#   for SQLite every file the driver really has open, the main one and each ATTACHed one, so a
#   `creator=`, `raw_connection()`, `pool.connect()` or an ATTACH in a connect listener is seen too)
#   is checked: one to any database but the one TEST_DATABASE_URL named when this block was imported,
#   or, when it was not set yet then, the first time a connection is checked (or, for SQLite, a file beside it or under pytest's `tmp_path` base for this run, such as a
#   per-test copy of the migrated template) is refused and recorded (SQLite may already have created
#   the empty file; nothing is written to it). SQLite files are checked when the connection is
#   opened, each time it is checked out of the pool, before every statement and when it is returned;
#   a file with a second hard link is refused wherever it is. Every SQLite connection from here on
#   through the stdlib (`sqlite3`, `sqlite3.dbapi2`, `_sqlite3`) or `pysqlite3` (a bare one too) is
#   judged the same way and gets, through the driver's own Connection type, an authorizer that
#   denies an ATTACH or VACUUM INTO of any file that is not the run's (one of its own is allowed). A run that re-points TEST_DATABASE_URL is refused too.
# - Before and after every test it resolves what the app's own session dependency hands a route (the
#   override the app holds for it, else the dependency itself) and fails the test unless that
#   session's bind (an Engine, or a Connection in a transactional fixture) is on TEST_DATABASE_URL's
#   database, or a refused connection was recorded.
# - v2: EVERY TEST RUNS IN ONE TRANSACTION THAT IS ROLLED BACK, whichever way the app opens its
#   session. Per test it takes one connection from the app's own engine (TEST_DB_SESSION_FACTORY's
#   bind: its listeners, connect args and options intact), begins an outer transaction, and binds the
#   app's own sessionmaker to that connection with `join_transaction_mode="create_savepoint"`,
#   through the factory's own `configure` (SQLAlchemy's "joining a Session into an external
#   transaction"). A route's `get_db`, a background task's `get_db_context()`, a job, and any module
#   that imported the factory all get sessions on that transaction; an app `commit()` commits a
#   savepoint. At teardown it restores the factory exactly, rolls back, closes the connection and
#   disposes the engine's pool, so no connection outlives the test's event loop (a pooled engine and
#   a per-test loop no longer collide). No `dependency_overrides`, no patched module attribute.
#   A connection the engine hands out to anything else during the test (code that opens its own
#   connection instead of a session from the factory) escapes the rollback, and fails the run.
# - A test that must REALLY commit (a durability lock: write, then read from a separate connection)
#   requests the block's `real_commits` fixture. Its sessions commit for real on the test database,
#   and after it the block empties the app's tables, every one but `alembic_version`, as Django's
#   TransactionTestCase does. So nothing a test needs may be seeded once for the whole run: seed it
#   per test. SQLite: DELETE. Postgres: TRUNCATE ... RESTART IDENTITY CASCADE of the tables no
#   extension owns (an extension's own tables, PostGIS's `spatial_ref_sys` and TimescaleDB's catalog
#   and chunks among them, and everything in a schema an extension created, are left as they are),
#   each TimescaleDB continuous aggregate first, as TimescaleDB requires. It waits at most 10 seconds
#   for its locks, then fails naming the sessions that hold them (a transaction a test or the app
#   left open), where it would otherwise hang the run.
# - It refuses to run unless the factory's engine is on TEST_DATABASE_URL's database (the host,
#   port and database read from the URL and its query; an unresolvable host matches only the same
#   URL), and has no `binds=` mapping. The tests run on function-scoped event loops (the canonical
#   pytest config): a test on a session-scoped loop gets a connection from another loop, and the
#   driver says so ("attached to a different loop").
# - v5: ON POSTGRES THE SERVER SAYS WHERE A CONNECTION IS. TEST_DATABASE_URL's text does not: a query
#   `host`, a `hostaddr` in a psycopg `conninfo` or psycopg2 `dsn`, PGHOSTADDR or a PGSERVICE file
#   each sent the driver to another server while the text named the test one. The test block
#   (`make test`, v10) creates the role `luxarch_test_server_<token>` on each Postgres server it
#   starts, from inside that server's own container, named by the run's token (handed to the suite
#   as LUXARCH_TEST_RUN); production never has it, and another run's server has another name. Every new driver connection any SQLAlchemy engine
#   opens on a Postgres dialect (judged by its type: a `timescaledb://` URL's too), the suite's
#   alembic migration and the app's engine among them, is asked, before anything else is sent on it
#   (as the dialect opens it, `do_connect`, after the app's own `do_connect` listeners and before the
#   pool's `first_connect`; one an engine's `creator=` opens, when the pool connects it; one opened
#   before this block was imported, at
#   its first checkout), for `current_database()` and whether that role exists (`to_regrole`, which
#   needs no catalog privilege; outside `make test`, LUXARCH_TEST_RUN unset, the hand-made role
#   `luxarch_test_server`), and is refused unless it does and the database is the run's: the one TEST_DATABASE_URL names in its PATH (a URL
#   with none there is refused), `<db>_<worker>` in an xdist worker. A connection opened before
#   TEST_DATABASE_URL is set is refused (nothing says yet which database is the run's), and so is a
#   Postgres connection in a run whose database is not on Postgres. The same question is asked on
#   the test's held connection, on the connection `real_commits` empties through, and on the
#   maintenance connection before a worker's DROP/CREATE DATABASE. Outside `make test`, give the
#   throwaway server the role yourself: `CREATE ROLE luxarch_test_server NOLOGIN`.
# - Under `pytest -n` each worker runs on its own database, `<db>_<worker>` (`<db>` is
#   TEST_DATABASE_URL's), and the block creates it: before the first test, its session fixture
#   `luxarch_worker_database`, in a process xdist started as a worker (its config's `workerinput`),
#   connects to the same server's maintenance database (`postgres`) through TEST_DATABASE_URL's own
#   driver, confirms the server is the test stack's (above), runs `DROP DATABASE IF EXISTS` and
#   `CREATE DATABASE` for that one name, then disposes the engine. Nothing else may connect there.
#   The suite's own session fixture then builds the schema in it, and requests
#   `luxarch_worker_database` so it runs after. A serial run (a pytest a worker's test starts
#   included), and a SQLite one, create nothing.
# - At the end of the session it fails the run if any connection was refused (so one opened after the
#   last test, in a thread or a teardown, still fails it), or if any engine still alive is on another
#   database (a lifespan's SessionLocal, a module-level engine the app uses directly). It reports
#   through pytest's terminal reporter (standard error when the reporter is switched off); an xdist
#   worker hands it back to the controller as its `shouldfail`, and the run stops, failed, with it.
# - coverage.py keeps its data in a SQLite file it opens to save, append or combine (after pytest
#   returns, in the `pytest -n` controller, under pytest-cov). A connection coverage's own SQLite
#   layer opens (the stdlib `connect` called from the module `coverage.sqlitedb` itself) is not the
#   suite's: it is let through on its file (refused on one with a second hard link), with no
#   authorizer, since `combine` ATTACHes each parallel file by a bound name. Every other connection
#   is judged until the process exits.
#
# HOW TO WIRE IT (see: luxarch --doc FLEET-MAKEFILE-STANDARD, the test block)
#   1) Save this block ALONE as its own conftest.py one directory above the tests (the repo root, or
#      apps/<app>/ in a monorepo), so it covers every test path; keep the repo's own conftest.py and
#      its fixtures where they are. It reads TEST_DATABASE_URL when it is imported, or, if the
#      suite's own conftest sets it later, the first time a connection is checked. (Pasting it
#      into an existing conftest.py, below the lines that set TEST_DATABASE_URL, works too.)
#   2) Set the two imports below. Nothing else is configurable on purpose.
#
# NOTE FOR ADOPTERS: this block carries NO module-level imports (it is pasted below existing code,
# where an import is ruff E402), and it defines the hooks `pytest_runtest_setup` and
# `pytest_runtest_teardown`. If your conftest defines either too, keep ONE definition of each, and
# make it call `_luxarch_test_db_bound(item)`: Python keeps only the last definition of a name, and
# repo.makefile_test_db_harness checks that the last one calls it. Its end-of-session check is
# registered as a plugin from the first test, so it adds no `pytest_sessionfinish` of its own.
#
# WHAT IT DOES NOT CLAIM
# - A write an isolated test can read back may still be lost in production: every session in the test
#   shares one transaction, so a write on a session its owner closed (a background task on the
#   request's session) and nobody committed is visible to the test. A test that locks in a write
#   across a session boundary requests `real_commits`, where only a committed write is read back.
# - It checks WHERE the suite connects, not what it reads. A test that copies the dev database into
#   `tmp_path` and runs on the copy passes: nothing writes to the dev file (it reads dev data).
# - The drivers' `connect` is wrapped when this block runs: a `from sqlite3 import connect` bound
#   before it, a `sqlite3.Connection(...)` (or a subclass) built directly, SQL a factory subclass's
#   `__init__` runs after calling `sqlite3.Connection.__init__` directly (it is judged once the
#   connection returns), or a later `set_authorizer` that replaces this one is not seen by the
#   wrapper. A factory subclass with an `__init__` of its own, or a metaclass `__call__`, is handed back
#   as a subclass of it, so `type(conn) is C` does not hold for it (`isinstance` does); an in-memory
#   main connection is refused (an engine's connection is still checked at checkout). A
#   driver other than the stdlib's and pysqlite3 (apsw, …) is not wrapped at all.
# - SQL sent through a raw DBAPI cursor between two checks (after a checkout, before the next
#   statement through SQLAlchemy) is seen when the connection is returned to the pool: recorded, and
#   the run fails, but after the statement ran.
# - A test that drives coverage.py's own SQLite layer (`coverage.sqlitedb.SqliteDb`) at another
#   file is let through as coverage.
# - Two `pytest -n` runs at once on one TEST_DATABASE_URL share its worker databases (each worker's
#   creation drops the other run's): give each run its own server, as the test block does.
# - A Postgres connection made without SQLAlchemy (a bare `asyncpg.connect`, `psycopg.connect`) is
#   not asked where it is, nor is one in a process this block is not loaded in: a child started by
#   spawn or forkserver (Python 3.14's default on Linux) or as a subprocess (the alembic CLI), the
#   migration-test run (`--confcutdir`), a run with `--noconftest`. A Pool `connect` listener the
#   app registers on the Pool class with `insert=True` after this block runs before its question.
# - A server given the role `luxarch_test_server` by hand is trusted as a test server outside
#   `make test`: never create it on one that holds data you keep. A test service whose data
#   is the dev database's (an `extends:` of it, a bind mount, an external volume) is marked for the
#   length of its run (repo.test_stack_parity and the test block's isolation say why it must not be).

# --- EDIT THIS: the app object (owns dependency_overrides) and the session dependency its routes use.
TEST_DB_APP_IMPORT = "app.web.main:app"
TEST_DB_SESSION_DEPENDENCY = "app.db.database:get_db"
# The app's own sessionmaker / async_sessionmaker: every session the app opens comes from it, and every
# test binds it to that test's rolled-back transaction.
TEST_DB_SESSION_FACTORY = "app.db.database:async_session"
# -----------------------------------------------------------------------------------------------------

# Connections refused because they were to another database: (where, what it was on).
_LUXARCH_TEST_DB_REFUSED: list[str] = []
# TEST_DATABASE_URL as it was when this block was imported (or, unset then, when a connection is
# first checked): a run that re-points it is refused.
_LUXARCH_TEST_DB_AT_IMPORT: list[str] = []
# The pytest config of the run (set from the first test): its tmp_path factory gives this run's base.
_LUXARCH_TEST_DB_CONFIG: list[object] = []
# The driver connection the running test's transaction holds (empty between tests and in a
# `real_commits` test): any other checkout from the pool meanwhile escapes the rollback.
_LUXARCH_TEST_DB_HELD: list[object] = []
# What the running test may do: "held" (an isolated test, one transaction), "commits" (a
# `real_commits` test, or the block emptying its tables), or nothing (no test is running: a module- or
# session-scoped fixture, a thread between tests).
_LUXARCH_TEST_DB_MODE: list[str] = []
# The app's own engine (the factory's bind, its sync engine for an async one), recorded from the first
# test on: a write through it while no test's transaction is held commits for real.
_LUXARCH_TEST_DB_APP_ENGINE: list[object] = []
# How many refusals the last per-test check already reported, and the test that is running: each
# test reports only what happened since, named by the test it happened in (one refusal reported at
# every later test buried the test that caused it).
_LUXARCH_TEST_DB_REPORTED: list[int] = [0]
# The event loop an isolated async test's connection was opened on.
_LUXARCH_TEST_DB_LOOP: list[object] = []
_LUXARCH_TEST_DB_CURRENT: list[str] = [""]
# Each engine the block itself opened on the maintenance database to create an xdist worker's
# database (not an engine of the app's on another database at the end of the run), and the one it is
# creating with now: its connection is the one there that is not refused.
_LUXARCH_TEST_DB_ADMIN: list[object] = []
_LUXARCH_TEST_DB_CREATING: list[object] = []


def _luxarch_test_db_in_test(what: str) -> str:
    """A refusal, with the test it happened in."""
    where = _LUXARCH_TEST_DB_CURRENT[0]
    return f"{where}: {what}" if where else what


def _luxarch_test_db_load(path: str) -> object:
    import importlib

    mod, _, attr = path.partition(":")
    if not attr:
        raise ValueError(f"expected 'module:attribute', got {path!r}")
    obj: object = importlib.import_module(mod)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _luxarch_test_db_key(url: object) -> tuple[str, str | None, int | None, str | None]:
    """One database whatever the driver or the password; a SQLite file by its resolved path."""
    import os

    from sqlalchemy import make_url

    u = make_url(str(url) if not isinstance(url, str) else url)
    backend = u.get_backend_name()
    db = u.database
    if backend == "sqlite":
        if db and db != ":memory:":
            db = os.path.realpath(db)
        return backend, None, None, db

    def query(name: str) -> str | None:
        v = u.query.get(name)
        if isinstance(v, tuple):
            # several hosts (a libpq failover list): only this exact URL is that database
            return ",".join(v)
        return v

    host = u.host or query("host") or query("hostaddr")
    port_text = query("port")
    port = u.port or (int(port_text) if port_text and port_text.isdigit() else None)
    db = db or query("dbname") or query("database")
    if not host:
        # libpq falls back to PGHOST or a socket: nothing here says which server, so the key is
        # the whole URL, equal only to itself
        host = "?" + u.render_as_string(hide_password=True)
    return backend, host, port, db


def _luxarch_test_db_target() -> tuple[str, str | None, int | None, str | None] | None:
    """The run's database: TEST_DATABASE_URL as it was when the block was imported, or, unset then
    (the block in a conftest.py above the suite's own), as it is the first time this is asked."""
    import os

    if not _LUXARCH_TEST_DB_AT_IMPORT and os.environ.get("TEST_DATABASE_URL"):
        _LUXARCH_TEST_DB_AT_IMPORT.append(os.environ["TEST_DATABASE_URL"])
    target = _LUXARCH_TEST_DB_AT_IMPORT[0] if _LUXARCH_TEST_DB_AT_IMPORT else ""
    if not target:
        return None
    key = _luxarch_test_db_key(target)
    # under `pytest -n` each worker runs on its own database, `<db>_<worker>` (the convention
    # `luxlint --emit-config conftest` builds); a SQLite file is per run already
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    if worker and worker != "master" and key[0] != "sqlite" and key[3]:
        from sqlalchemy import make_url

        u = make_url(target)
        if u.database:
            # keyed as the URL naming that database (a host-less URL is keyed whole)
            return _luxarch_test_db_key(u.set(database=f"{u.database}_{worker}"))
        return key[0], key[1], key[2], f"{key[3]}_{worker}"
    return key


def _luxarch_test_db_repointed(now: str) -> bool:
    """TEST_DATABASE_URL now names another database than it did when the block read it (compared as
    read, before any xdist worker's suffix)."""
    first = _LUXARCH_TEST_DB_AT_IMPORT[0] if _LUXARCH_TEST_DB_AT_IMPORT else ""
    return bool(first) and _luxarch_test_db_key(now) != _luxarch_test_db_key(first)


def _luxarch_test_db_tmp_base() -> str:
    """This run's `tmp_path` base, as pytest's tmp_path factory holds it ("" before a test asked for
    one): `--basetemp` when given, else the numbered directory pytest made for the run."""
    import os

    config = _LUXARCH_TEST_DB_CONFIG[0] if _LUXARCH_TEST_DB_CONFIG else None
    factory = getattr(config, "_tmp_path_factory", None)
    base = getattr(factory, "_basetemp", None)
    return os.path.realpath(str(base)) if base is not None else ""


def _luxarch_test_db_ok(
    got: tuple[str, str | None, int | None, str | None] | None,
) -> bool:
    """`got` is the run's database: the one TEST_DATABASE_URL named, or, for SQLite, a file made for
    the run: in that file's own directory (made for the run, `tempfile.mkdtemp()`), or under this
    run's `tmp_path` base (a per-test copy of the migrated template)."""
    import os

    want = _luxarch_test_db_target()
    if want is None or got is None:
        return False
    if got == want:
        return True
    if want[0] != "sqlite" or got[0] != "sqlite" or not got[3] or got[3] == ":memory:":
        return False
    path = got[3]
    home = os.path.dirname(want[3]) if want[3] and want[3] != ":memory:" else ""
    if home and os.path.dirname(path) == home:
        return True
    base = _luxarch_test_db_tmp_base()
    return bool(base) and path.startswith(base + os.sep)


# The role the test block (`make test`) creates on each Postgres server it starts, from inside that
# server's own container: `luxarch_test_server_<token>`, named by the run's own token, which the suite
# gets as LUXARCH_TEST_RUN. A server carrying it is the one THIS run's test stack made: another run's
# stack, or a marker left on storage that outlived its run, has another name; production has none.
# Outside `make test` (no LUXARCH_TEST_RUN), a throwaway server gets `luxarch_test_server` by hand.
# Asked with `to_regrole`, which needs no privilege on any catalog, and no connection option makes a
# role exist.
_LUXARCH_TEST_DB_MARKER = "luxarch_test_server"


def _luxarch_test_db_marker() -> str | None:
    """The role that marks this run's server (None: LUXARCH_TEST_RUN is not a run token)."""
    import os
    import re

    run = os.environ.get("LUXARCH_TEST_RUN", "")
    if not run:
        return _LUXARCH_TEST_DB_MARKER
    return (
        f"{_LUXARCH_TEST_DB_MARKER}_{run}"
        if re.fullmatch(r"[a-z0-9]{8,40}", run)
        else None
    )


def _luxarch_test_db_identity() -> str:
    """The question a Postgres connection is asked: its database, and whether this run's marker
    role exists on its server."""
    marker = _luxarch_test_db_marker() or ""
    return (
        "SELECT pg_catalog.current_database(), "
        f"pg_catalog.to_regrole('{marker}') IS NOT NULL AND '{marker}' <> ''"
    )


def _luxarch_test_db_pg(dialect: object) -> bool:
    """`dialect` (a class or an instance) is SQLAlchemy's Postgres dialect or one built on it (a
    `timescaledb://`, `cockroachdb://` or `redshift+psycopg2://` URL's): judged by its type, never by
    the URL's backend name."""
    from sqlalchemy.dialects.postgresql.base import PGDialect

    kind = dialect if isinstance(dialect, type) else type(dialect)
    return issubclass(kind, PGDialect) or getattr(dialect, "name", None) == "postgresql"


def _luxarch_test_db_is_postgres() -> bool:
    """The run's database is on Postgres: TEST_DATABASE_URL's dialect is Postgres's or built on it.
    A dialect that cannot be loaded is not known not to be."""
    from sqlalchemy import make_url

    if _luxarch_test_db_target() is None:
        return False
    try:
        dialect = make_url(_LUXARCH_TEST_DB_AT_IMPORT[0]).get_dialect()
    except Exception:
        return True
    return _luxarch_test_db_pg(dialect)


def _luxarch_test_db_run_database() -> str | None:
    """The run's Postgres database: the one TEST_DATABASE_URL names in its PATH (`<db>_<worker>` in
    an xdist worker), or None when it names none there."""
    import os

    from sqlalchemy import make_url

    if _luxarch_test_db_target() is None:
        return None
    # read from the URL object: no local holds TEST_DATABASE_URL's text, so no traceback prints it
    database = make_url(_LUXARCH_TEST_DB_AT_IMPORT[0]).database
    if not database:
        return None
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    return f"{database}_{worker}" if worker and worker != "master" else database


def _luxarch_test_db_identity_refusal(row: object, allow: tuple[str, ...] = ()) -> str:
    """Why a Postgres connection whose server answered `row` (its `current_database()`, and whether
    this run's marker role exists there) is not the run's database ("" when it is). `allow` names a
    database the block itself may also use (the maintenance one, to create a worker's)."""
    want = _luxarch_test_db_run_database()
    if want is None:
        return (
            "a Postgres connection, and TEST_DATABASE_URL names no database in its path: the "
            "driver would pick one (libpq: the one named after the user). Name the test database "
            "in the URL's path"
        )
    marker = _luxarch_test_db_marker()
    if marker is None:
        return "LUXARCH_TEST_RUN is not a run token (8-40 lowercase letters and digits)"
    if not isinstance(row, (tuple, list)) or len(row) != 2:
        return f"a Postgres connection whose server could not say where it is ({row!r})"
    database, marked = row
    if marked is not True:
        return (
            f"a connection to database {database!r} on a server without this run's marker (the "
            f"role {marker}, which `make test` creates on the Postgres server it starts, named by "
            "the run's token): the driver reached another server than the run's test one, another "
            "run's, or one whose storage outlived the run that marked it. Run the suite with "
            f"`make test`, or on a throwaway server you made, `CREATE ROLE {_LUXARCH_TEST_DB_MARKER} "
            "NOLOGIN` (with LUXARCH_TEST_RUN unset)"
        )
    if database != want and database not in allow:
        return f"a connection to database {database!r}, not the run's {want!r}"
    return ""


def _luxarch_test_db_ask_dbapi(dbapi_conn: object) -> object:
    """The identity row of a Postgres driver connection, asked on that connection, which is then left
    as it was found: the transaction the question began (a driver not in autocommit) is ended, so the
    dialect can still set its isolation level."""
    make = getattr(dbapi_conn, "cursor", None)
    if not callable(make):
        return None
    cursor = make()
    try:
        cursor.execute(_luxarch_test_db_identity())
        row = cursor.fetchone()
    finally:
        cursor.close()
    end = getattr(dbapi_conn, "rollback", None)
    if callable(end):
        end()
    return tuple(row) if row is not None else None


def _luxarch_test_db_confirm(row: object, allow: tuple[str, ...] = ()) -> None:
    """Refuse, and record, a Postgres connection that is not on the run's database."""
    what = _luxarch_test_db_identity_refusal(row, allow)
    if what:
        _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
        raise AssertionError(f"luxarch test-db-bound: refused {what}")


def _luxarch_test_db_bind_url(session: object) -> object:
    """The URL a session's bind is on, whether it is bound to an Engine or to a Connection (the
    transactional-test fixture: `Session(bind=connection)`)."""
    getter = getattr(session, "get_bind", None)
    bind = getter() if callable(getter) else None
    url = getattr(bind, "url", None)
    if url is None:
        url = getattr(getattr(bind, "engine", None), "url", None)
    # None: no bind at all (a test's recording fake), which is no database, so nothing to judge
    return url


def _luxarch_test_db_files(dbapi_conn: object) -> list[str]:
    """Every file a SQLite DBAPI connection has open that is not the run's ("" files are in memory):
    the main database and each ATTACHed one, as the driver reports them."""
    import os

    make = getattr(dbapi_conn, "cursor", None)
    cursor = make() if callable(make) else None
    if cursor is None:
        return []
    try:
        cursor.execute("PRAGMA database_list")
        rows = cursor.fetchall()
    finally:
        cursor.close()
    out = []
    for row in rows:
        name, file = str(row[1]), str(row[2] or "")
        if name == "temp" or (not file and name != "main"):
            continue
        got = ("sqlite", None, None, os.path.realpath(file) if file else ":memory:")
        if not _luxarch_test_db_ok(got):
            out.append(f"{name} {got}")
        elif file and os.path.exists(file) and os.stat(file).st_nlink > 1:
            # a hard link in the run's directory is another file's bytes under a new name
            out.append(f"{name} {got} (hard-linked: another name holds the same file)")
    return out


def _luxarch_test_db_attach_ok(file: object) -> bool:
    """A file an ATTACH or VACUUM INTO names is the run's: in memory, or a file the run's database may
    be (beside it, under this run's tmp_path base) with no second hard link. A `file:` URI is judged
    by its path. None (SQLite hands no name for a bound or computed one) is never the run's."""
    import os
    from urllib.parse import parse_qsl, unquote, urlsplit

    if file is None:
        return False
    name = str(file)
    if name.startswith("file:"):
        parts = urlsplit(name)
        modes = [
            v for k, v in parse_qsl(parts.query, keep_blank_values=True) if k == "mode"
        ]
        if modes == ["memory"]:
            return True
        name = unquote(parts.path)
    if name in ("", ":memory:"):
        return True
    path = os.path.realpath(name)
    if not _luxarch_test_db_ok(("sqlite", None, None, path)):
        return False
    return not (os.path.exists(path) and os.stat(path).st_nlink > 1)


def _luxarch_test_db_authorize(action: int, file: object = None, *_: object) -> int:
    """The SQLite authorizer every stdlib connection gets: an ATTACH (VACUUM INTO attaches its target
    too) of a file that is not the run's is denied, whatever runs it (SQLAlchemy, a raw DBAPI
    cursor, the stdlib); one of the run's own files is allowed."""
    import sqlite3

    if action == sqlite3.SQLITE_ATTACH and not _luxarch_test_db_attach_ok(file):
        _LUXARCH_TEST_DB_REFUSED.append(
            f"an ATTACH or VACUUM INTO of {file!r}, not the run's, denied by the SQLite driver"
        )
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _luxarch_test_db_from_coverage(caller: object) -> bool:
    """`caller` (the frame that called the driver's `connect`) is coverage.py's own SQLite layer: the
    module `coverage.sqlitedb` as imported (`coverage.sqldata` before coverage 7), judged by its
    module namespace itself, never by a name a frame carries."""
    import sys

    namespace = getattr(caller, "f_globals", None)
    for name in ("coverage.sqlitedb", "coverage.sqldata"):
        module = sys.modules.get(name)
        if module is not None and namespace is vars(module):
            return True
    return False


class _LuxarchTestDbDriver:
    """One SQLite driver's `connect`, judged: refused on another file, and given the authorizer
    through the driver's own Connection type (a subclass that overrides `set_authorizer` cannot
    skip it)."""

    def __init__(self, module: object) -> None:
        self.original = vars(module)["connect"]
        self.kind = vars(module)["Connection"]
        self.guards: dict[type, type] = {}

    def guard(self, factory: type) -> type:
        """`factory` (the driver's Connection, or a subclass of it) with the authorizer installed
        the moment the driver opens the file: before a subclass's `__init__` can run SQL."""
        made = self.guards.get(factory)
        if made is not None:
            return made
        kind = self.kind

        def init(conn: object, *args: object, **kwargs: object) -> None:
            kind.__init__(conn, *args, **kwargs)  # the driver's own: it opens the file
            kind.set_authorizer(conn, _luxarch_test_db_authorize)

        guarded = type("_LuxarchTestDbGuarded", (kind,), {"__init__": init})
        made = (
            guarded
            if factory is kind
            else type(
                f"_LuxarchTestDbJudged{getattr(factory, '__name__', '')}",
                (factory, guarded),
                {},
            )
        )
        self.guards[factory] = made
        return made

    def coverage(self, *args: object, **kwargs: object) -> object:
        """coverage.py's own connection, on its file: refused when that file has a second hard link
        (another file's bytes). It gets no authorizer: `combine` ATTACHes each parallel data file
        by a bound name, which an authorizer cannot read."""
        import os

        conn = self.original(*args, **kwargs)
        cursor = conn.cursor()
        try:
            cursor.execute("PRAGMA database_list")
            files = [str(r[2] or "") for r in cursor.fetchall()]
        finally:
            cursor.close()
        linked = [
            f for f in files if f and os.path.exists(f) and os.stat(f).st_nlink > 1
        ]
        if linked:
            conn.close()
            what = f"coverage.py's connection on {linked[0]!r}, a file with a second hard link"
            _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
            raise AssertionError(f"luxarch test-db-bound: refused {what}")
        return conn

    def connect(self, *args: object, **kwargs: object) -> object:
        want = _luxarch_test_db_target()
        if want is None or want[0] != "sqlite":
            return self.original(*args, **kwargs)
        import inspect

        here = inspect.currentframe()
        if _luxarch_test_db_from_coverage(here.f_back if here is not None else None):
            return self.coverage(*args, **kwargs)
        if _LUXARCH_TEST_DB_MODE[:1] == ["held"]:
            what = (
                "a raw SQLite driver connection during an isolated test: it commits outside the "
                f"test's rolled-back transaction; use a session from {TEST_DB_SESSION_FACTORY}"
            )
            _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
            raise AssertionError(f"luxarch test-db-bound: refused {what}")
        factory = kwargs.get("factory", args[5] if len(args) > 5 else self.kind)
        if not (isinstance(factory, type) and issubclass(factory, self.kind)):
            what = f"a SQLite connection whose factory {factory!r} is not the driver's Connection"
            _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
            raise AssertionError(f"luxarch test-db-bound: refused {what}")
        # a factory with an `__init__` of its own could run SQL before this returns: it gets the
        # authorizer the moment the driver opens the file; one without keeps its exact type
        own_init = any(
            "__init__" in vars(c)
            for c in factory.__mro__
            if issubclass(c, self.kind) and c is not self.kind
        ) or any(
            "__init__" in vars(c)
            for c in factory.__mro__
            if not issubclass(c, self.kind) and c is not object
        )
        # a metaclass `__call__` runs code around construction too
        if own_init or type(factory).__call__ is not type.__call__:
            if len(args) > 5:
                args = (*args[:5], self.guard(factory), *args[6:])
            else:
                kwargs = {**kwargs, "factory": self.guard(factory)}
        conn = self.original(*args, **kwargs)
        # and always once it returns: an `__init__` that calls the driver's own `__init__` directly
        # skips the guarded one
        self.kind.set_authorizer(conn, _luxarch_test_db_authorize)
        bad = _luxarch_test_db_files(conn)
        if not isinstance(conn, self.kind):
            bad.append(
                f"a connection of type {type(conn).__name__}, not the driver's own"
            )
        if bad:
            conn.close()
            what = f"a SQLite connection on {', '.join(bad)}, not the TEST_DATABASE_URL database {want}"
            _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
            raise AssertionError(f"luxarch test-db-bound: refused {what}")
        return conn


def _luxarch_test_db_wrap_sqlite3() -> None:
    """Every SQLite connection the process opens from here on through the stdlib (`sqlite3`,
    `sqlite3.dbapi2`, `_sqlite3`: SQLAlchemy's pysqlite, aiosqlite's worker thread, a bare call) or
    `pysqlite3` is judged like an engine's: refused on another file, and given the authorizer."""
    import importlib

    drivers: list[tuple[str, tuple[str, ...]]] = [
        ("_sqlite3", ("sqlite3", "sqlite3.dbapi2", "_sqlite3")),
        ("pysqlite3._sqlite3", ("pysqlite3", "pysqlite3.dbapi2", "pysqlite3._sqlite3")),
    ]
    for core, names in drivers:
        try:
            spaces = [vars(importlib.import_module(n)) for n in (core, *names)]
        except ImportError:
            continue
        current = spaces[0].get("connect")
        if isinstance(getattr(current, "__self__", None), _LuxarchTestDbDriver):
            continue
        driver = _LuxarchTestDbDriver(importlib.import_module(core))
        for space in spaces:
            if "connect" in space:
                space["connect"] = driver.connect


def _luxarch_test_db_listen() -> None:
    """Refuse, and record, every connection an engine opens to another database."""
    import os
    import re

    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from sqlalchemy.pool import Pool

    def _check(conn: object) -> None:
        engine = getattr(conn, "engine", None)
        if any(engine is a for a in _LUXARCH_TEST_DB_CREATING):
            return
        url = getattr(engine, "url", None)
        want = _luxarch_test_db_target()
        got = _luxarch_test_db_key(url) if url is not None else None
        if not _luxarch_test_db_ok(got):
            what = f"a connection to {got}, not the TEST_DATABASE_URL database {want}"
            _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
            raise AssertionError(f"luxarch test-db-bound: refused {what}")

    def _check_dbapi(dbapi_conn: object, *_: object) -> None:
        # the connection the driver really has (a `creator=`, `raw_connection()`, `pool.connect()`
        # never pass through engine_connect): for SQLite, every file it has open, ATTACHed ones too
        want = _luxarch_test_db_target()
        if want is None or want[0] != "sqlite":
            return
        bad = _luxarch_test_db_files(dbapi_conn)
        if bad:
            what = f"a driver connection on {', '.join(bad)}, not the TEST_DATABASE_URL database {want}"
            _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
            raise AssertionError(f"luxarch test-db-bound: refused {what}")

    def _check_statement(
        conn: object, cursor: object, statement: object, *_: object
    ) -> None:
        want = _luxarch_test_db_target()
        if want is None or want[0] != "sqlite":
            return
        # comments and whitespace before the first keyword are not SQL
        head = re.sub(r"^(\s+|--[^\n]*\n?|/\*.*?\*/)*", "", str(statement), flags=re.S)
        if re.match(r"attach\b|vacuum\b[^;]*\binto\b", head, re.I | re.S):
            # the file it names, when written as a literal, may be one of the run's own
            named = re.match(
                r"(?:attach\s+(?:database\s+)?|vacuum\b[^;']*?\binto\s+)'((?:[^']|'')*)'",
                head,
                re.I | re.S,
            )
            target = named.group(1).replace("''", "'") if named else None
            if target is None or not _luxarch_test_db_attach_ok(target):
                what = f"an ATTACH or VACUUM INTO ({str(statement)[:80]!r}): the run's database is {want}"
                _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
                raise AssertionError(f"luxarch test-db-bound: refused {what}")
        pooled = getattr(conn, "connection", None)
        _check_dbapi(getattr(pooled, "dbapi_connection", None))

    def _check_held(dbapi_conn: object, *_: object) -> None:
        # a test runs in one transaction: any other connection the pool hands out meanwhile is code
        # that opens its own connection instead of a session from the app's factory, and it commits
        # outside the rollback
        if _LUXARCH_TEST_DB_HELD and dbapi_conn is not _LUXARCH_TEST_DB_HELD[0]:
            what = (
                "a connection outside the test's rolled-back transaction: code that opens its own "
                f"connection or engine instead of a session from {TEST_DB_SESSION_FACTORY}"
            )
            _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
            raise AssertionError(f"luxarch test-db-bound: refused {what}")

    def _check_loop(conn: object, *_: object) -> None:
        # the test's transaction lives on the connection opened in the test's own loop; a statement
        # on it from another running loop (starlette's sync TestClient runs the app on a portal
        # thread's loop) fails inside the driver with nothing naming the cause
        if not _LUXARCH_TEST_DB_LOOP or _LUXARCH_TEST_DB_MODE[:1] != ["held"]:
            return
        import asyncio

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            return
        if running is not _LUXARCH_TEST_DB_LOOP[0]:
            what = (
                "the app ran on another event loop than the test's (starlette's sync TestClient runs "
                "it on a portal thread): an async app's tests drive it with "
                "`httpx.AsyncClient(transport=ASGITransport(app=app))`, which shares the test's "
                "transaction"
            )
            _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
            raise AssertionError(f"luxarch test-db-bound: refused {what}")

    def _check_outside(
        conn: object, cursor: object, statement: object, *_: object
    ) -> None:
        # a module- or session-scoped fixture, or a thread after a test, writing through the app's
        # own engine commits for real: the next tests see it (seed per test, inside its transaction)
        if _LUXARCH_TEST_DB_MODE or not _LUXARCH_TEST_DB_APP_ENGINE:
            return
        if getattr(conn, "engine", None) is not _LUXARCH_TEST_DB_APP_ENGINE[0]:
            return
        head = re.sub(r"^(\s+|--[^\n]*\n?|/\*.*?\*/)*", "", str(statement), flags=re.S)
        if re.match(r"(insert|update|delete|merge|replace|upsert)\b", head, re.I):
            what = (
                f"a write through the app's engine outside any test's transaction "
                f"({str(statement)[:60]!r}): a module- or session-scoped fixture, or a thread, "
                "commits it for real; seed per test"
            )
            _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
            raise AssertionError(f"luxarch test-db-bound: refused {what}")

    def _check_identity(
        dbapi_conn: object, record: object, *_: object, dialect: object = None
    ) -> None:
        # a Postgres run: the server itself says where the connection is, asked on it before
        # anything is sent on it (TEST_DATABASE_URL's text does not decide: a query `host`, a
        # `hostaddr` in a conninfo, PGHOSTADDR or a service file send the driver elsewhere). Asked
        # as the dialect opens it (`do_connect`), and, for one an engine's `creator=` opened or one
        # made before the block listened, when the pool connects it and at its first checkout.
        # The engine's own dialect: a Postgres connection in a run whose database is not on
        # Postgres is judged too
        if dialect is None:
            dialect = getattr(
                getattr(record, "_ConnectionRecord__pool", None), "_dialect", None
            )
        if _luxarch_test_db_target() is None:
            # TEST_DATABASE_URL not set yet: nothing says which database is the run's
            what = (
                "a connection opened before TEST_DATABASE_URL was set: set it before any engine "
                "connects (a raw_connection() or pool.connect() too)"
            )
            _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
            raise AssertionError(f"luxarch test-db-bound: refused {what}")
        if not _luxarch_test_db_is_postgres():
            if dialect is not None and _luxarch_test_db_pg(dialect):
                what = "a Postgres connection in a run whose TEST_DATABASE_URL is not on Postgres"
                _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
                raise AssertionError(f"luxarch test-db-bound: refused {what}")
            return
        info = getattr(record, "info", None)
        if isinstance(info, dict) and info.get(_LUXARCH_TEST_DB_MARKER):
            return
        try:
            row = _luxarch_test_db_ask_dbapi(dbapi_conn)
        except Exception as e:
            row = f"{type(e).__name__} asking it"
        # the block's own engine on the maintenance database, while it creates a worker's
        allow = ("postgres",) if _LUXARCH_TEST_DB_CREATING else ()
        try:
            _luxarch_test_db_confirm(row, allow)
        except AssertionError:
            import contextlib

            with contextlib.suppress(Exception):
                getattr(dbapi_conn, "close", lambda: None)()
            raise
        if isinstance(info, dict):
            info[_LUXARCH_TEST_DB_MARKER] = True

    def _check_do_connect(
        dialect: object, record: object, cargs: object, cparams: object
    ) -> object:
        # the dialect opens the driver connection here and the server is asked on it, before the
        # pool's `first_connect` (the dialect's own initialize, an app's one-time setup) sends
        # anything, and outside the lock SQLAlchemy holds while `first_connect` runs: a question
        # asked under that lock deadlocks two connections opened at once on one event loop
        connect = getattr(dialect, "connect", None)
        if not callable(connect) or not isinstance(cargs, (list, tuple)):
            return None
        # the rest of the chain first, as SQLAlchemy would run it (an app's listener that sets the
        # password or returns a connection of its own), then the dialect, then the question
        dbapi_conn = None
        for fn in getattr(getattr(dialect, "dispatch", None), "do_connect", ()):
            # this listener, and another copy of this block's (imported under a second name)
            if getattr(fn, "__name__", "") == _check_do_connect.__name__:
                continue
            dbapi_conn = fn(dialect, record, cargs, cparams)
            if dbapi_conn is not None:
                break
        if dbapi_conn is None:
            dbapi_conn = connect(
                *cargs, **(cparams if isinstance(cparams, dict) else {})
            )
        _check_identity(dbapi_conn, record, dialect=dialect)
        return dbapi_conn

    def _check_returned(dbapi_conn: object, record: object) -> None:
        import contextlib

        if dbapi_conn is None:
            return
        # recorded: the run fails at its end; a check-in must not raise
        with contextlib.suppress(AssertionError):
            _check_dbapi(dbapi_conn)

    if not _LUXARCH_TEST_DB_AT_IMPORT and os.environ.get("TEST_DATABASE_URL"):
        _LUXARCH_TEST_DB_AT_IMPORT.append(os.environ["TEST_DATABASE_URL"])
    _luxarch_test_db_wrap_sqlite3()
    if not event.contains(Engine, "do_connect", _check_do_connect):
        event.listen(Engine, "do_connect", _check_do_connect, insert=True)
    if not event.contains(Engine, "engine_connect", _check):
        event.listen(Engine, "engine_connect", _check)
    if not event.contains(Engine, "before_cursor_execute", _check_statement):
        event.listen(Engine, "before_cursor_execute", _check_statement)
    if not event.contains(Engine, "before_cursor_execute", _check_outside):
        event.listen(Engine, "before_cursor_execute", _check_outside)
    if not event.contains(Engine, "before_cursor_execute", _check_loop):
        event.listen(Engine, "before_cursor_execute", _check_loop)
    for name, fn in (
        ("connect", _check_identity),
        ("checkout", _check_identity),
        ("connect", _check_dbapi),
        ("checkout", _check_dbapi),
        ("checkout", _check_held),
        ("checkin", _check_returned),
    ):
        if not event.contains(Pool, name, fn):
            # the server is asked first, before an app's own connect listener sends anything
            event.listen(Pool, name, fn, insert=fn is _check_identity)


def _luxarch_test_db_session_url(dep: object) -> object:
    """The URL of the engine behind the session `dep` hands a route."""
    import asyncio
    import inspect

    if not callable(dep):
        raise AssertionError(f"{TEST_DB_SESSION_DEPENDENCY} is not callable")
    if inspect.isasyncgenfunction(dep):

        async def _take() -> object:
            agen = dep()
            try:
                session = await agen.__anext__()
                return _luxarch_test_db_bind_url(session)
            finally:
                await agen.aclose()

        return asyncio.run(_take())
    if inspect.isgeneratorfunction(dep):
        gen = dep()
        try:
            return _luxarch_test_db_bind_url(next(gen))
        finally:
            gen.close()
    if inspect.iscoroutinefunction(dep):

        async def _call() -> object:
            return _luxarch_test_db_bind_url(await dep())

        return asyncio.run(_call())
    return _luxarch_test_db_bind_url(dep())


def _luxarch_test_db_is_async() -> bool:
    """Whether TEST_DB_SESSION_FACTORY is an async_sessionmaker (decided by its type, before either
    fixture binds it)."""
    # by the class's own name and module: importing sqlalchemy.ext.asyncio needs greenlet, which a
    # sync-only app need not have
    kinds = type(_luxarch_test_db_load(TEST_DB_SESSION_FACTORY)).__mro__
    return any(
        k.__name__ == "async_sessionmaker"
        and k.__module__.startswith("sqlalchemy.ext.asyncio")
        for k in kinds
    )


def _luxarch_test_db_factory() -> tuple[object, dict[str, object], object]:
    """(the factory, its kw, its engine): TEST_DB_SESSION_FACTORY, checked to be a sessionmaker whose one
    engine is on TEST_DATABASE_URL's database."""
    factory = _luxarch_test_db_load(TEST_DB_SESSION_FACTORY)
    kw = getattr(factory, "kw", None)
    if not isinstance(kw, dict) or not callable(getattr(factory, "configure", None)):
        raise AssertionError(
            f"{TEST_DB_SESSION_FACTORY} is not a sessionmaker or async_sessionmaker"
        )
    if kw.get("binds"):
        raise AssertionError(
            f"{TEST_DB_SESSION_FACTORY} routes models to engines through `binds=`: give it one "
            "engine (`bind=`), so one transaction can hold every session a test opens"
        )
    bind = kw.get("bind")
    url = getattr(bind, "url", None)
    if url is None:
        raise AssertionError(f"{TEST_DB_SESSION_FACTORY} has no engine bound to it")
    want = _luxarch_test_db_target()
    got = _luxarch_test_db_key(url)
    if want is None or got != want:
        raise AssertionError(
            f"{TEST_DB_SESSION_FACTORY} is on {got}, not the TEST_DATABASE_URL database {want}: "
            "configure the app on the test database in the test environment"
        )
    return factory, kw, bind


def _luxarch_test_db_empty_sql(dialect: str, rows: list[tuple[str, str]]) -> list[str]:
    """The statements that empty these tables (schema, name), `alembic_version` kept."""
    quoted = [
        ".".join('"' + part.replace('"', '""') + '"' for part in (schema, name) if part)
        for schema, name in rows
        if name != "alembic_version"
    ]
    if not quoted:
        return []
    if dialect == "postgresql":
        return [f"TRUNCATE {', '.join(quoted)} RESTART IDENTITY CASCADE"]
    if dialect == "sqlite":
        return [
            "PRAGMA foreign_keys = OFF",
            *(f"DELETE FROM {q}" for q in quoted),
            "PRAGMA foreign_keys = ON",
        ]
    raise AssertionError(
        f"real_commits empties Postgres and SQLite databases, not {dialect}"
    )


_LUXARCH_TEST_DB_TABLES = {
    # the app's tables: no extension owns them (`pg_depend` deptype 'e': PostGIS's spatial_ref_sys,
    # TimescaleDB's catalog) or the schema they are in (TimescaleDB's chunks and materializations),
    # and none is another session's temporary table
    "postgresql": (
        "SELECT n.nspname, c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p') AND c.relpersistence <> 't' "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND n.nspname NOT LIKE 'pg\\_toast%' "
        "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.deptype = 'e' AND ("
        "(d.classid = 'pg_class'::regclass AND d.objid = c.oid) OR "
        "(d.classid = 'pg_namespace'::regclass AND d.objid = n.oid)))"
    ),
    "sqlite": "SELECT '', name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
}
# TimescaleDB refuses to TRUNCATE a hypertable a continuous aggregate reads from; truncating the
# aggregates first (their materializations live in TimescaleDB's own schema) is what it accepts
_LUXARCH_TEST_DB_CAGGS = (
    "SELECT view_schema, view_name FROM timescaledb_information.continuous_aggregates"
)
_LUXARCH_TEST_DB_LOCK_TIMEOUT = "10s"
_LUXARCH_TEST_DB_LOCK_HOLDERS = (
    "SELECT pid, state, app, query, clock_timestamp() - xact_start FROM ("
    "SELECT DISTINCT a.pid, a.state, coalesce(a.application_name, '') AS app, "
    "left(coalesce(a.query, ''), 80) AS query, a.xact_start "
    "FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
    "WHERE l.granted AND l.relation IS NOT NULL AND a.pid <> pg_backend_pid() "
    "AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())"
    ") h ORDER BY xact_start"
)


def _luxarch_test_db_empty_server(c: object) -> None:
    """Empty the app's tables on a server database, in the transaction `c` (a SQLAlchemy Connection)
    is in. On Postgres it waits for its locks at most `_LUXARCH_TEST_DB_LOCK_TIMEOUT`, then fails
    naming the sessions holding them, where an idle transaction would otherwise hang the run."""
    from sqlalchemy import text
    from sqlalchemy.engine import Connection
    from sqlalchemy.exc import DBAPIError

    if not isinstance(c, Connection):
        raise AssertionError(
            f"luxarch test-db-bound: real_commits empties through a SQLAlchemy Connection, not {c!r}"
        )
    run = c.execute
    name = c.dialect.name
    if _luxarch_test_db_pg(c.dialect):
        # asked on the very connection that empties, before anything is emptied
        _luxarch_test_db_confirm(tuple(run(text(_luxarch_test_db_identity())).one()))
        name = "postgresql"
    if name not in _LUXARCH_TEST_DB_TABLES:
        raise AssertionError(
            f"luxarch test-db-bound: real_commits empties Postgres and SQLite databases, not {name}: "
            "its committed rows would reach every later test"
        )
    if name != "postgresql":
        rows = [(r[0], r[1]) for r in run(text(_LUXARCH_TEST_DB_TABLES[name]))]
        for sql in _luxarch_test_db_empty_sql(name, rows):
            run(text(sql))
        return
    run(text(f"SET LOCAL lock_timeout = '{_LUXARCH_TEST_DB_LOCK_TIMEOUT}'"))
    # an app engine's shorter statement_timeout would cancel the wait first, naming nobody
    run(text("SET LOCAL statement_timeout = 0"))
    rows = [(r[0], r[1]) for r in run(text(_LUXARCH_TEST_DB_TABLES["postgresql"]))]
    has_tsdb = run(
        text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).first()
    caggs = (
        [(r[0], r[1]) for r in run(text(_LUXARCH_TEST_DB_CAGGS))] if has_tsdb else []
    )
    statements = (
        [
            "TRUNCATE "
            + ", ".join(
                ".".join('"' + p.replace('"', '""') + '"' for p in pair)
                for pair in caggs
            )
        ]
        if caggs
        else []
    )
    statements += _luxarch_test_db_empty_sql("postgresql", rows)
    try:
        with c.begin_nested():
            for sql in statements:
                run(text(sql))
    except DBAPIError as e:
        orig = getattr(e, "orig", None)
        # psycopg and asyncpg: `sqlstate`; psycopg2: `pgcode`; pg8000: the server's fields, `C`
        fields = (getattr(orig, "args", None) or [None])[0]
        code = (
            getattr(orig, "sqlstate", None)
            or getattr(orig, "pgcode", None)
            or (fields.get("C") if isinstance(fields, dict) else None)
        )
        if code != "55P03":  # lock_not_available
            raise
        holders = [
            f"pid {r[0]} ({r[1]}, {r[2] or 'no application_name'}, open {r[4]}): {r[3]!r}"
            for r in run(text(_LUXARCH_TEST_DB_LOCK_HOLDERS))
        ]
        what = (
            f"real_commits could not empty the tables within {_LUXARCH_TEST_DB_LOCK_TIMEOUT}: "
            "another session holds a lock on them, a transaction a test or the app opened and "
            "never ended (an idle session, a connection a fixture kept). Holding: "
            + ("; ".join(holders) or "none still listed")
        )
        _luxarch_test_db_fail_teardown(what)


_LUXARCH_TEST_DB_SQLITE_RECIPE = (
    "the SQLite driver did not begin a transaction, so a savepoint would commit for real: install "
    "SQLAlchemy's pysqlite recipe on the app's engine, `@event.listens_for(engine.sync_engine, "
    '"connect")` setting `dbapi_conn.isolation_level = None` and `@event.listens_for(engine.sync_engine, '
    '"begin")` running `conn.exec_driver_sql("BEGIN")` (for a sync engine, on the engine itself)'
)


def _luxarch_test_db_in_transaction(dbapi: object) -> bool:
    """Whether a SQLite driver connection has really begun a transaction (aiosqlite wraps it)."""
    for cand in (dbapi, getattr(dbapi, "_connection", None)):
        flag = getattr(cand, "in_transaction", None)
        if isinstance(flag, bool):
            return flag
    return False


def _luxarch_test_db_empty_sqlite(raw: object) -> None:
    """Empty every table but `alembic_version` on a SQLite driver connection (the pool's proxy for
    it), outside any transaction, so `PRAGMA foreign_keys = OFF` takes effect (inside one it does
    nothing)."""
    from sqlalchemy.pool import PoolProxiedConnection

    if not isinstance(raw, PoolProxiedConnection):
        raise AssertionError(
            f"luxarch test-db-bound: real_commits empties through the pool's connection, not {raw!r}"
        )
    cur = raw.cursor()
    cur.execute("PRAGMA foreign_keys")
    row = cur.fetchone()
    was = int(row[0]) if row else 0
    cur.execute("PRAGMA foreign_keys = OFF")
    cur.execute(_LUXARCH_TEST_DB_TABLES["sqlite"])
    rows = [(r[0], r[1]) for r in cur.fetchall()]
    for sql in _luxarch_test_db_empty_sql("sqlite", rows):
        if not sql.startswith("PRAGMA"):
            cur.execute(sql)
    raw.commit()
    cur.execute(f"PRAGMA foreign_keys = {was}")
    cur.close()


def _luxarch_test_db_check_held(dialect: str, dbapi: object, identity: object) -> None:
    """The test's own connection is what the block believes: a SQLite driver inside a transaction, a
    Postgres server the test stack marked, on the run's database (`identity`, the server's answer to
    `_luxarch_test_db_identity()` on that connection: a `connect_args`, `creator` or query can point
    elsewhere)."""
    if dialect == "sqlite" and not _luxarch_test_db_in_transaction(dbapi):
        raise AssertionError(f"luxarch test-db-bound: {_LUXARCH_TEST_DB_SQLITE_RECIPE}")
    if dialect == "postgresql":
        _luxarch_test_db_confirm(identity)


def _luxarch_test_db_fail_teardown(what: str) -> None:
    """Fail the test whose teardown found `what`, and the run, reported once (not again at the next
    test's setup)."""
    _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))
    _LUXARCH_TEST_DB_REPORTED[0] = len(_LUXARCH_TEST_DB_REFUSED)
    raise AssertionError(f"luxarch test-db-bound: {what}")


def _luxarch_test_db_worker_statements(u: object, name: str) -> list[str]:
    """The statements that make `name` a fresh empty database on TEST_DATABASE_URL's server. `u` is
    the URL object, never its text: a refusal's traceback prints its arguments, and a URL object
    prints its password masked."""
    from sqlalchemy.engine import URL

    if not isinstance(u, URL):
        raise TypeError(
            "luxarch test-db-bound: the worker statements take a URL object"
        )
    if not u.database or any(k in u.query for k in ("dbname", "database")):
        raise AssertionError(
            "luxarch test-db-bound: under pytest -n the block creates each worker's database, so "
            "TEST_DATABASE_URL names its database in the URL's path, once"
        )
    if len(name.encode()) > 63:
        # Postgres cuts a longer name to 63 bytes, so two workers' names would be one database
        raise AssertionError(
            f"luxarch test-db-bound: the worker database {name!r} is longer than Postgres's 63-byte "
            "names: shorten TEST_DATABASE_URL's database name"
        )
    quoted = '"' + name.replace('"', '""') + '"'
    return [f"DROP DATABASE IF EXISTS {quoted}", f"CREATE DATABASE {quoted}"]


def _luxarch_test_db_create_worker_database(config: object) -> str:
    """This xdist worker's database, `<db>_<worker>`, created fresh on TEST_DATABASE_URL's server
    through its maintenance database, with TEST_DATABASE_URL's own driver; its name, or "" when there
    is nothing to create (a serial run, a SQLite database). Only a process xdist started as a worker
    (`config.workerinput`) creates one: a pytest a worker's test starts inherits the worker's
    environment, and dropping the worker's database under it would empty it mid-run."""
    import os

    want = _luxarch_test_db_target()
    workerinput = getattr(config, "workerinput", None)
    if want is None or not isinstance(workerinput, dict) or want[0] == "sqlite":
        return ""
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    if workerinput.get("workerid") != worker:
        raise AssertionError(
            f"luxarch test-db-bound: xdist started this worker as {workerinput.get('workerid')!r}, "
            f"but PYTEST_XDIST_WORKER is {worker!r}: the run's database cannot be told"
        )
    if not _luxarch_test_db_is_postgres():
        raise AssertionError(
            "luxarch test-db-bound: under pytest -n each worker runs on its own database, which "
            f"the block creates on Postgres, not {want[0]}"
        )
    import asyncio

    from sqlalchemy import create_engine, make_url, text
    from sqlalchemy.pool import NullPool

    name = str(want[3])
    # a URL object, never its text: the password stays out of every local a traceback can print
    run_url = make_url(_LUXARCH_TEST_DB_AT_IMPORT[0])
    statements = _luxarch_test_db_worker_statements(run_url, name)
    admin_url = run_url.set(database="postgres")
    if admin_url.get_dialect().is_async:
        from sqlalchemy.ext.asyncio import create_async_engine

        aengine = create_async_engine(
            admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
        _LUXARCH_TEST_DB_ADMIN.append(aengine.sync_engine)
        _LUXARCH_TEST_DB_CREATING[:] = [aengine.sync_engine]

        async def _create() -> None:
            try:
                async with aengine.connect() as c:
                    # the server says it is the test stack's, before any DDL is sent to it
                    identity = (
                        await c.execute(text(_luxarch_test_db_identity()))
                    ).one()
                    _luxarch_test_db_confirm(tuple(identity), ("postgres",))
                    for sql in statements:
                        await c.execute(text(sql))
            finally:
                await aengine.dispose()

        try:
            asyncio.run(_create())
        finally:
            _LUXARCH_TEST_DB_CREATING.clear()
        return name
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    _LUXARCH_TEST_DB_ADMIN.append(engine)
    _LUXARCH_TEST_DB_CREATING[:] = [engine]
    try:
        with engine.connect() as c:
            identity = c.execute(text(_luxarch_test_db_identity())).one()
            _luxarch_test_db_confirm(tuple(identity), ("postgres",))
            for sql in statements:
                c.execute(text(sql))
    finally:
        _LUXARCH_TEST_DB_CREATING.clear()
        engine.dispose()
    return name


def _luxarch_test_db_fixtures() -> tuple[object, object, object, object]:
    """The block's fixtures, built here so the block imports nothing at module level: the autouse
    isolation for an async factory and for a sync one (each passes through for the other kind),
    `real_commits`, the flag a test requests to commit for real, and `luxarch_worker_database`, the
    session fixture that creates an xdist worker's database."""
    from collections.abc import AsyncIterator, Iterator

    import pytest

    def held(dbapi: object) -> None:
        _LUXARCH_TEST_DB_HELD[:] = [dbapi]

    @pytest.fixture(scope="session", autouse=True)
    def luxarch_worker_database(request: pytest.FixtureRequest) -> str:
        """Under `pytest -n`: this worker's database, created before the suite's own session
        fixtures build its schema ("" when serial or SQLite)."""
        return _luxarch_test_db_create_worker_database(request.config)

    @pytest.fixture
    def real_commits() -> None:
        """Request it in a test that must commit for real: its tables are emptied after it."""
        return None

    @pytest.fixture(autouse=True)
    def _luxarch_test_db_isolated_sync(request: object) -> Iterator[None]:
        from sqlalchemy import text
        from sqlalchemy.engine import Engine

        if not TEST_DB_SESSION_FACTORY or _luxarch_test_db_is_async():
            yield
            return
        _factory, kw, bind = _luxarch_test_db_factory()
        if not isinstance(bind, Engine):
            raise AssertionError(
                f"{TEST_DB_SESSION_FACTORY} is bound to {bind!r}, not an Engine"
            )
        saved = dict(kw)
        commits = "real_commits" in getattr(request, "fixturenames", ())
        conn = trans = None
        escaped = False
        try:
            if commits:
                _LUXARCH_TEST_DB_MODE[:] = ["commits"]
            else:
                conn = bind.connect()
                trans = conn.begin()
                pg = _luxarch_test_db_pg(bind.dialect)
                identity = (
                    tuple(conn.execute(text(_luxarch_test_db_identity())).one())
                    if pg
                    else None
                )
                _luxarch_test_db_check_held(
                    "postgresql" if pg else bind.dialect.name,
                    conn.connection.dbapi_connection,
                    identity,
                )
                held(conn.connection.dbapi_connection)
                _LUXARCH_TEST_DB_MODE[:] = ["held"]
                kw.update(bind=conn, join_transaction_mode="create_savepoint")
            yield
        finally:
            # every step runs whatever an earlier one raised: a skipped restore or dispose leaks a
            # bound factory or a connection into every later test
            kw.clear()
            kw.update(saved)
            _LUXARCH_TEST_DB_HELD.clear()
            try:
                if trans is not None and conn is not None:
                    escaped = not trans.is_active
                    try:
                        trans.rollback()
                    finally:
                        conn.close()
            finally:
                try:
                    bind.dispose()
                    if commits:
                        # emptied on a fresh connection, after the test's own are gone
                        _LUXARCH_TEST_DB_MODE[:] = ["commits"]
                        try:
                            if bind.dialect.name == "sqlite":
                                raw = bind.raw_connection()
                                try:
                                    _luxarch_test_db_empty_sqlite(raw)
                                finally:
                                    raw.close()
                            else:
                                with bind.begin() as c:
                                    _luxarch_test_db_empty_server(c)
                        finally:
                            # a failed emptying too: a pooled connection outlives the test
                            bind.dispose()
                finally:
                    _LUXARCH_TEST_DB_MODE.clear()
            if escaped:
                what = (
                    "the test's outer transaction was committed by app code "
                    "(`Connection.commit()`): commit the session, never its connection"
                )
                _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))

    try:
        import pytest_asyncio
    except ImportError:
        return (
            real_commits,
            _luxarch_test_db_isolated_sync,
            None,
            luxarch_worker_database,
        )

    async def _luxarch_test_db_isolated_async(request: object) -> AsyncIterator[None]:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncEngine

        if not TEST_DB_SESSION_FACTORY or not _luxarch_test_db_is_async():
            yield
            return
        _factory, kw, bind = _luxarch_test_db_factory()
        if not isinstance(bind, AsyncEngine):
            raise AssertionError(
                f"{TEST_DB_SESSION_FACTORY} is bound to {bind!r}, not an AsyncEngine"
            )
        saved = dict(kw)
        commits = "real_commits" in getattr(request, "fixturenames", ())
        conn = trans = None
        escaped = False
        try:
            if commits:
                _LUXARCH_TEST_DB_MODE[:] = ["commits"]
            else:
                conn = await bind.connect()
                trans = await conn.begin()
                raw = await conn.get_raw_connection()
                pg = _luxarch_test_db_pg(bind.dialect)
                identity = (
                    tuple((await conn.execute(text(_luxarch_test_db_identity()))).one())
                    if pg
                    else None
                )
                _luxarch_test_db_check_held(
                    "postgresql" if pg else bind.dialect.name,
                    raw.dbapi_connection,
                    identity,
                )
                held(raw.dbapi_connection)
                import asyncio

                _LUXARCH_TEST_DB_LOOP[:] = [asyncio.get_running_loop()]
                _LUXARCH_TEST_DB_MODE[:] = ["held"]
                kw.update(bind=conn, join_transaction_mode="create_savepoint")
            yield
        finally:
            # every step runs whatever an earlier one raised: a skipped restore or dispose leaks a
            # bound factory or a connection into every later test
            kw.clear()
            kw.update(saved)
            _LUXARCH_TEST_DB_HELD.clear()
            _LUXARCH_TEST_DB_LOOP.clear()
            try:
                if trans is not None and conn is not None:
                    escaped = not trans.is_active
                    try:
                        await trans.rollback()
                    finally:
                        await conn.close()
            finally:
                try:
                    try:
                        await bind.dispose()
                    except (
                        Exception
                    ):  # a connection of another loop: dropped, and reported
                        await bind.dispose(close=False)
                        _LUXARCH_TEST_DB_REFUSED.append(
                            _luxarch_test_db_in_test(
                                "a connection opened on another event loop (a sync TestClient) could not be closed "
                                "from the test's: drive an async app with httpx.AsyncClient"
                            )
                        )
                    if commits:
                        # emptied on a fresh connection, after the test's own are gone
                        _LUXARCH_TEST_DB_MODE[:] = ["commits"]
                        try:
                            if bind.dialect.name == "sqlite":
                                async with bind.connect() as c:
                                    await c.run_sync(
                                        lambda sc: _luxarch_test_db_empty_sqlite(
                                            sc.connection
                                        )
                                    )
                            else:
                                async with bind.begin() as c:
                                    await c.run_sync(_luxarch_test_db_empty_server)
                        finally:
                            # a failed emptying too: a pooled connection outlives the test's loop
                            await bind.dispose()
                finally:
                    _LUXARCH_TEST_DB_MODE.clear()
            if escaped:
                what = (
                    "the test's outer transaction was committed by app code "
                    "(`Connection.commit()`): commit the session, never its connection"
                )
                _LUXARCH_TEST_DB_REFUSED.append(_luxarch_test_db_in_test(what))

    try:
        # the connection belongs to the loop that opened it: the test's own, function-scoped one
        isolated_async = pytest_asyncio.fixture(autouse=True, loop_scope="function")(
            _luxarch_test_db_isolated_async
        )
    except (
        TypeError
    ):  # pytest-asyncio before loop_scope (0.24): it refuses the keyword when applied
        isolated_async = pytest_asyncio.fixture(autouse=True)(
            _luxarch_test_db_isolated_async
        )
    return (
        real_commits,
        _luxarch_test_db_isolated_sync,
        isolated_async,
        luxarch_worker_database,
    )


def _luxarch_test_db_problems() -> list[str]:
    want = _luxarch_test_db_target()
    if want is None:
        return [
            "TEST_DATABASE_URL is not set: there is no throwaway database to be bound to"
        ]
    import os

    out = list(_LUXARCH_TEST_DB_REFUSED[_LUXARCH_TEST_DB_REPORTED[0] :])
    _LUXARCH_TEST_DB_REPORTED[0] = len(_LUXARCH_TEST_DB_REFUSED)
    now = os.environ.get("TEST_DATABASE_URL") or ""
    if now and _luxarch_test_db_repointed(now):
        out.append(
            f"TEST_DATABASE_URL was re-pointed during the run, to {_luxarch_test_db_key(now)}: the run's "
            f"database is {want}"
        )
    app = _luxarch_test_db_load(TEST_DB_APP_IMPORT)
    dep = _luxarch_test_db_load(TEST_DB_SESSION_DEPENDENCY)
    overrides = getattr(app, "dependency_overrides", {})
    used = overrides.get(dep, dep)
    url = _luxarch_test_db_session_url(used)
    got = _luxarch_test_db_key(url) if url is not None else None
    if url is not None and not _luxarch_test_db_ok(got):
        out.append(
            f"the session the app's {TEST_DB_SESSION_DEPENDENCY} hands a route is on {got}, not the "
            f"TEST_DATABASE_URL database {want}"
            + ("" if used is not dep else " (the app holds no override for it)")
        )
    return out


class _LuxarchTestDbFinish:
    """The end-of-session check, registered as a plugin so the block defines no session hook."""

    def pytest_sessionfinish(self, session: object, exitstatus: object) -> None:
        import gc
        import os
        import sys

        import pytest
        from sqlalchemy.engine import Engine

        if not isinstance(session, pytest.Session):
            raise AssertionError(
                f"luxarch test-db-bound: pytest handed {session!r}, not its Session"
            )

        want = _luxarch_test_db_target()
        problems = sorted(set(_LUXARCH_TEST_DB_REFUSED))
        now = os.environ.get("TEST_DATABASE_URL") or ""
        if want is not None and now and _luxarch_test_db_repointed(now):
            problems.append(
                f"TEST_DATABASE_URL was re-pointed during the run, to {_luxarch_test_db_key(now)}: "
                f"the run's database is {want}"
            )
        # every engine still alive at the end: one on another database is code that writes there
        # the moment it is used (a lifespan's SessionLocal, a module-level engine). Judged by its
        # type alone: `isinstance` also reads an object's `__class__`, which can run code (torch's
        # deprecated `reduce_op` warns there, an error under `filterwarnings = error`)
        problems += sorted(
            {
                f"an engine this process built is on {_luxarch_test_db_key(e.url)}, not the "
                f"TEST_DATABASE_URL database {want}"
                for e in gc.get_objects()
                if issubclass(type(e), Engine)
                and not any(e is a for a in _LUXARCH_TEST_DB_ADMIN)
                and not _luxarch_test_db_ok(_luxarch_test_db_key(e.url))
            }
        )
        if problems:
            report = (
                "luxarch test-db-bound: FAIL — the suite is not on its throwaway database:\n  - "
                + "\n  - ".join(problems)
            )
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            if reporter is not None:
                reporter.write_line("")
                reporter.write_line(report, red=True)
            else:
                sys.stderr.write(f"\n{report}\n")
            session.exitstatus = 1
            workeroutput = getattr(session.config, "workeroutput", None)
            if isinstance(workeroutput, dict):
                # an xdist worker's exit status and output never reach the controller; what it
                # hands back in `shouldfail` does, and the controller stops the run on it, failed,
                # with this text
                workeroutput["shouldfail"] = report


def _luxarch_test_db_bound(item: object, teardown: bool = False) -> None:
    """Before each test: what happened since the last test (a refusal, an override's session on
    another database) fails THIS test, naming where it happened. Nothing is checked at teardown: a
    hook that raises there skips pytest's own teardown (the test's monkeypatch, this block's
    rollback), and the test's own fakes are still in place; the next test's check, and the run's end,
    see the state after it."""
    config = getattr(item, "config", None)
    if config is not None and not _LUXARCH_TEST_DB_CONFIG:
        _LUXARCH_TEST_DB_CONFIG.append(config)
    manager = getattr(config, "pluginmanager", None)
    # `-p no:luxarch-test-db-bound` blocks the name, and `register` then returns None: it would
    # switch off the end-of-run check
    if (
        manager is not None
        and not manager.has_plugin("luxarch-test-db-bound")
        and manager.register(_LuxarchTestDbFinish(), "luxarch-test-db-bound") is None
    ):
        raise AssertionError(
            "luxarch test-db-bound: its plugin `luxarch-test-db-bound` is blocked (`-p no:…`): "
            "the run cannot be judged; remove the option"
        )
    if teardown:
        # a refusal raised during this test already failed it: not reported again at the next one
        # (a refusal recorded later, in a fixture's teardown, still is, named by this test)
        _LUXARCH_TEST_DB_REPORTED[0] = len(_LUXARCH_TEST_DB_REFUSED)
        return
    if TEST_DB_SESSION_FACTORY and not _LUXARCH_TEST_DB_APP_ENGINE:
        _f, _kw, app_bind = _luxarch_test_db_factory()
        _LUXARCH_TEST_DB_APP_ENGINE.append(getattr(app_bind, "sync_engine", app_bind))
    before = _LUXARCH_TEST_DB_CURRENT[0]
    _LUXARCH_TEST_DB_CURRENT[0] = str(getattr(item, "nodeid", ""))
    problems = _luxarch_test_db_problems()
    if problems:
        where = f" (during or after {before})" if before else ""
        raise AssertionError(
            f"luxarch test-db-bound: the suite is not on its throwaway database{where}:\n  - "
            + "\n  - ".join(problems)
        )


def pytest_runtest_setup(item: object) -> None:
    _luxarch_test_db_bound(item)


def pytest_runtest_teardown(item: object, nextitem: object) -> None:
    _luxarch_test_db_bound(item, teardown=True)


_luxarch_test_db_listen()
(
    real_commits,
    _luxarch_test_db_isolated_sync,
    _luxarch_test_db_isolated_async,
    luxarch_worker_database,
) = _luxarch_test_db_fixtures()
