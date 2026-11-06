# luxarch:test-db-identity asset v3 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit test-db-identity`.
"""Where a Postgres connection really is, asked of the server on that connection.

TEST_DATABASE_URL's text does not say where the driver lands: a query `host`, a `hostaddr` in a
psycopg `conninfo` or psycopg2 `dsn`, PGHOSTADDR or a PGSERVICE file each send it to another server
while the text names the test one. So the server is asked. The test block (`make test`) creates the
role `luxarch_test_server_<token>` (NOLOGIN) on each Postgres server it starts, from inside that
server's own container, named by the run's token, which the suite gets as LUXARCH_TEST_RUN.
Production never has that role, and another run's server has another name. Outside `make test`
(LUXARCH_TEST_RUN unset) a throwaway server is given `luxarch_test_server` by hand.

Every new driver connection any SQLAlchemy engine opens on a Postgres dialect (judged by its type:
a `timescaledb://` URL's too) is asked, before anything else is sent on it, for
`current_database()` and whether that role exists (`to_regrole`, which needs no catalog privilege),
under a `statement_timeout` of its own where the driver is not in autocommit, and asked again when
the connection's own timeout cancels it on a cold server process. It is asked as the dialect opens it
(`do_connect`, after the app's own `do_connect` listeners and before the pool's `first_connect`);
one an engine's `creator=` opens, when the pool connects it; one opened before this module was
installed, at its first checkout. What the answer must be depends on
the process's scope, LUXARCH_TEST_DB_SCOPE, read once:

- `suite` (the default; the suite's own run and every process it starts): the role exists and the
  database is the run's, the one TEST_DATABASE_URL names in its PATH (a URL with none there is
  refused), `<db>_<worker>` in an xdist worker. A connection opened before TEST_DATABASE_URL is set
  is refused, and so is a Postgres connection in a run whose database is not on Postgres.
- `server` (the test block's migration-test run, which builds databases of its own): the role
  exists; any database on that server.
- `none` (a required tail that needs no database): every SQLAlchemy connection is refused, on any
  dialect, before the driver is called.

A refused connection is closed, recorded, and raised as an AssertionError. The run fails at its end
on any refusal: this process's own, and each one a process the suite started recorded in the
directory LUXARCH_TEST_DB_REFUSALS names (made when pytest configures, inherited by every child).

How it is loaded: the test block passes `-p luxarch_test_db.identity` to both its pytest runs, and
prepends `luxarch_test_db/children` to PYTHONPATH, whose `sitecustomize` installs this module in
every Python process the run starts: a child started by spawn or forkserver (Python 3.14's default
on Linux), a subprocess CLI (alembic), an xdist worker. A pytest run that registers this module (by
`-p`, or through `luxarch_test_db.bound`) installs it when it registers.

Every process the run starts is handed the check as it starts (an audit hook, which only ever
adds and never refuses): a process started with no environment of its own inherits this one, which
gets `luxarch_test_db/children` back first on its PYTHONPATH and the run's LUXARCH_TEST_* settings
back; one `subprocess` starts with a dict environment of its own gets them put into that
environment. A test may change the environment as it likes (`mock.patch.dict(os.environ)`,
`os.environ.clear()`): the next process it starts gets them back.

What it does not claim: a Postgres connection made without SQLAlchemy (a bare `asyncpg.connect`,
`psycopg.connect`) is not asked. Nor is one in a process started in a way that skips
`sitecustomize` or drops what the hook hands it, which takes doing it on purpose:
- a wrapper that clears or replaces the environment it passes on (`env -u PYTHONPATH`, `env -i`,
  `env PYTHONPATH=...`), and a Python started with `-E`, `-I` or `-S`, directly or behind a wrapper
  (`timeout`, `nice`), or with those flags on a script's shebang line (`#!/usr/bin/python -E`,
  `#!/usr/bin/env -S python3 -I`);
- `subprocess` with `executable=` naming Python while argv[0] names something else, given those
  flags;
- an environment of its own that is not a dict (a `MappingProxyType`), or one passed to
  `os.posix_spawn` or `os.exec*`, which drops `children` or the LUXARCH_TEST_* settings;
- a C-level `unsetenv` (through `ctypes`) that `os.environ` never sees, and a multiprocessing
  child started (multiprocessing raises no audit event) after a test removed PYTHONPATH or the
  LUXARCH_TEST_* settings from `os.environ` and before any other process start put them back.
A Pool `connect` listener the app registers on the Pool class with `insert=True` after this module
runs before its question. A server given the role `luxarch_test_server` by hand is trusted outside
`make test`: never create it on one that holds data you keep.

PYTEST_DONT_REWRITE: `sitecustomize` imports this module before pytest starts, and pytest would warn
that it cannot rewrite its asserts (it has none), an error under the fleet's `filterwarnings`.
"""

import contextlib
import os
import re
import sys
import tempfile
from typing import Protocol, TypeGuard, runtime_checkable

from sqlalchemy import event, make_url
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.engine import Engine
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.pool import Pool

# The role the test block creates on each Postgres server it starts: `luxarch_test_server_<token>`,
# named by the run's own token. A server carrying it is the one THIS run's test stack made: another
# run's stack, or a marker left on storage that outlived its run, has another name; production has
# none. Asked with `to_regrole`, which needs no privilege on any catalog, and no connection option
# makes a role exist.
_LUXARCH_TEST_DB_MARKER = "luxarch_test_server"
_LUXARCH_TEST_DB_SCOPES = ("suite", "server", "none")

# Connections refused, each with the test it happened in.
_LUXARCH_TEST_DB_REFUSED: list[str] = []
# TEST_DATABASE_URL as it was when this module was installed (or, unset then, when a connection is
# first checked): a run that re-points it is refused.
_LUXARCH_TEST_DB_AT_IMPORT: list[str] = []
# The test that is running (set by luxarch_test_db.bound), named in each refusal.
_LUXARCH_TEST_DB_CURRENT: list[str] = [""]
# The engine luxarch_test_db.bound is creating an xdist worker's database with: its connection is
# the one on the maintenance database that is not refused.
_LUXARCH_TEST_DB_CREATING: list[object] = []
# The process's scope, read once.
_LUXARCH_TEST_DB_SCOPE: list[str] = []


def _luxarch_test_db_in_test(what: str) -> str:
    """A refusal, with the test it happened in."""
    where = _LUXARCH_TEST_DB_CURRENT[0]
    return f"{where}: {what}" if where else what


def _luxarch_test_db_scope() -> str:
    """LUXARCH_TEST_DB_SCOPE, as the process first read it: `suite` when unset."""

    if not _LUXARCH_TEST_DB_SCOPE:
        _LUXARCH_TEST_DB_SCOPE.append(
            os.environ.get("LUXARCH_TEST_DB_SCOPE", "") or "suite"
        )
    return _LUXARCH_TEST_DB_SCOPE[0]


def _luxarch_test_db_log(what: str) -> None:
    """Record a refusal where the run's pytest reads it at its end: this process may be a child whose
    failure nobody looks at (a pool worker whose exception the app catches)."""

    where = os.environ.get("LUXARCH_TEST_DB_REFUSALS", "")
    if not where:
        return
    # unwritable: the refusal is still raised in this process
    with (
        contextlib.suppress(OSError),
        open(
            os.path.join(where, f"{os.getpid()}-{len(_LUXARCH_TEST_DB_REFUSED)}"),
            "w",
            encoding="utf-8",
        ) as f,
    ):
        f.write(what)


def _luxarch_test_db_refuse(what: str) -> None:
    """Record the refusal (here, and for the run's pytest) and raise it."""
    said = _luxarch_test_db_in_test(what)
    _LUXARCH_TEST_DB_REFUSED.append(said)
    _luxarch_test_db_log(said)
    raise AssertionError(f"luxarch test-db-bound: refused {what}")


def _luxarch_test_db_logged() -> list[str]:
    """Every refusal a process of this run recorded in LUXARCH_TEST_DB_REFUSALS."""

    where = os.environ.get("LUXARCH_TEST_DB_REFUSALS", "")
    out: list[str] = []
    if not where or not os.path.isdir(where):
        return out
    for name in sorted(os.listdir(where)):
        try:
            with open(os.path.join(where, name), encoding="utf-8") as f:
                out.append(f.read())
        except OSError as e:
            out.append(f"a refusal recorded in {name} could not be read ({e})")
    return out


def _luxarch_test_db_key(url: object) -> tuple[str, str | None, int | None, str | None]:
    """One database whatever the driver or the password; a SQLite file by its resolved path."""

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
    """The run's database: TEST_DATABASE_URL as it was when this module was installed, or, unset
    then (a suite conftest sets it), as it is the first time this is asked."""

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
        u = make_url(target)
        if u.database:
            # keyed as the URL naming that database (a host-less URL is keyed whole)
            return _luxarch_test_db_key(u.set(database=f"{u.database}_{worker}"))
        return key[0], key[1], key[2], f"{key[3]}_{worker}"
    return key


def _luxarch_test_db_marker() -> str | None:
    """The role that marks this run's server (None: LUXARCH_TEST_RUN is not a run token)."""

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

    kind = dialect if isinstance(dialect, type) else type(dialect)
    return issubclass(kind, PGDialect) or getattr(dialect, "name", None) == "postgresql"


def _luxarch_test_db_other_dialect(dialect: object) -> bool:
    """`dialect` is a SQLAlchemy dialect known not to be Postgres's (a pool built without an engine
    has none, and is not known not to be)."""

    return isinstance(dialect, Dialect) and not _luxarch_test_db_pg(dialect)


def _luxarch_test_db_is_postgres() -> bool:
    """The run's database is on Postgres: TEST_DATABASE_URL's dialect is Postgres's or built on it.
    A dialect that cannot be loaded is not known not to be."""

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
    this run's marker role exists there) is not where this process may connect ("" when it is).
    `allow` names a database the suite may also use (the maintenance one, to create a worker's).
    In the `server` scope any database on a marked server is."""
    scope = _luxarch_test_db_scope()
    want = _luxarch_test_db_run_database() if scope == "suite" else ""
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
    if want and database != want and database not in allow:
        return f"a connection to database {database!r}, not the run's {want!r}"
    return ""


# How long the question may take, whatever `statement_timeout` the connection carries (an app's
# `server_settings` or `options`, a role's or database's default): a short one of the app's own
# would cancel the question and refuse a connection that is where it should be, and none at all
# would let an unresponsive server hang the run.
_LUXARCH_TEST_DB_ASK_TIMEOUT = "10s"


# Tries at the question. The first statement a fresh server process runs pays its warm-up
# (TimescaleDB's loader looks its extension up: about 2 ms on an idle host, far more on a loaded
# one), so a short timeout of the connection's own can cancel whatever is sent first; the next try
# runs warm. Only a cancellation is tried again.
_LUXARCH_TEST_DB_ASK_TRIES = 3


def _luxarch_test_db_cancelled(e: BaseException) -> bool:
    """`e` is the server cancelling a statement (SQLSTATE 57014), as psycopg, psycopg2 or
    SQLAlchemy's asyncpg adapter raise it."""
    return "57014" in (getattr(e, "sqlstate", None), getattr(e, "pgcode", None))


def _luxarch_test_db_in_transaction(dbapi_conn: object) -> bool:
    """The driver connection is already inside a transaction (an app's `do_connect` listener ran a
    statement on it): psycopg's `info.transaction_status`, psycopg2's `get_transaction_status()`,
    asyncpg's `is_in_transaction()` behind SQLAlchemy's adapter. False when none says so."""

    with contextlib.suppress(Exception):
        info = getattr(dbapi_conn, "info", None)
        status = getattr(info, "transaction_status", None)
        if status is not None:
            return int(status) != 0
        get = getattr(dbapi_conn, "get_transaction_status", None)
        if callable(get):
            return int(get()) != 0
        inner = getattr(dbapi_conn, "_connection", None)
        asks = getattr(inner, "is_in_transaction", None)
        if callable(asks):
            return bool(asks())
    return False


def _luxarch_test_db_forget_failed_begin(dbapi_conn: object) -> None:
    """asyncpg keeps a transaction whose `BEGIN` the server cancelled as the connection's open one
    (its `_top_xact`, in a failed state), and SQLAlchemy's asyncpg adapter, which tracks a transaction
    only once its `BEGIN` succeeds, has nothing to roll back: the next try's `BEGIN` would go out as a
    `SAVEPOINT`, outside any transaction. Where the server says none is open, that record is dropped."""

    with contextlib.suppress(Exception):
        inner = getattr(dbapi_conn, "_connection", None)
        asks = getattr(inner, "is_in_transaction", None)
        if (
            inner is not None
            and getattr(inner, "_top_xact", None) is not None
            and callable(asks)
            and not asks()
        ):
            inner._top_xact = None


def _luxarch_test_db_ask_dbapi(dbapi_conn: object) -> object:
    """The identity row of a Postgres driver connection, asked on that connection, which is then left
    as it was found. A driver not in autocommit asks inside the transaction its first statement
    opens, under a `statement_timeout` of the question's own (`SET LOCAL`), and that transaction is
    rolled back, its timeout with it, so the dialect can still set its isolation level. A driver in
    autocommit is asked one statement at a time, opening no transaction of its own (one held open
    across round trips would meet the connection's `idle_in_transaction_session_timeout`). A try
    the connection's own timeout cancels is made again on the warmed process, up to
    _LUXARCH_TEST_DB_ASK_TRIES times; anything else, or the last cancellation, is raised and the
    connection refused; a try whose `BEGIN` itself was cancelled leaves the driver idle for the next
    (`_luxarch_test_db_forget_failed_begin`). A connection handed over already inside a transaction (an app's `do_connect`
    listener's statement) is asked in a savepoint that is rolled back, so that transaction is kept.
    Stated limit: a connection timeout shorter than a warm question (well under
    a millisecond) still refuses it."""
    make = getattr(dbapi_conn, "cursor", None)
    if not callable(make):
        return None
    autocommit = getattr(dbapi_conn, "autocommit", False) is True
    # a transaction the app already opened is kept: the question runs in a savepoint inside it
    nested = not autocommit and _luxarch_test_db_in_transaction(dbapi_conn)
    end = getattr(dbapi_conn, "rollback", None)
    for attempt in range(_LUXARCH_TEST_DB_ASK_TRIES):
        cursor = make()
        try:
            try:
                if nested:
                    cursor.execute("SAVEPOINT luxarch_test_db_ask")
                if not autocommit:
                    cursor.execute(
                        f"SET LOCAL statement_timeout = '{_LUXARCH_TEST_DB_ASK_TIMEOUT}'"
                    )
                cursor.execute(_luxarch_test_db_identity())
                row = cursor.fetchone()
            except Exception as e:
                if (
                    attempt + 1 < _LUXARCH_TEST_DB_ASK_TRIES
                    and _luxarch_test_db_cancelled(e)
                ):
                    continue
                raise
            return tuple(row) if row is not None else None
        finally:
            try:
                if nested:
                    # the savepoint's `SET LOCAL` ends with it; the app's transaction stays
                    cursor.execute("ROLLBACK TO SAVEPOINT luxarch_test_db_ask")
                    cursor.execute("RELEASE SAVEPOINT luxarch_test_db_ask")
            finally:
                cursor.close()
                # the question's transaction ends, its timeout with it, however the question went
                if not autocommit and not nested and callable(end):
                    end()
                if not autocommit and not nested:
                    _luxarch_test_db_forget_failed_begin(dbapi_conn)
    return None


def _luxarch_test_db_confirm(row: object, allow: tuple[str, ...] = ()) -> None:
    """Refuse, and record, a Postgres connection that is not where this process may connect."""
    what = _luxarch_test_db_identity_refusal(row, allow)
    if what:
        _luxarch_test_db_refuse(what)


def _luxarch_test_db_close(dbapi_conn: object) -> None:
    """Close a refused driver connection; one that will not close is refused all the same."""

    with contextlib.suppress(Exception):
        getattr(dbapi_conn, "close", lambda: None)()


def _luxarch_test_db_unscoped() -> str:
    """Why this process may open no connection at all ("" when it may open some)."""
    scope = _luxarch_test_db_scope()
    if scope == "none":
        return (
            "a database connection in a run that needs no database (LUXARCH_TEST_DB_SCOPE=none): "
            "a test that needs one belongs in the suite `make test` runs"
        )
    if scope not in _LUXARCH_TEST_DB_SCOPES:
        return (
            f"a connection under LUXARCH_TEST_DB_SCOPE={scope!r}, which is none of "
            f"{', '.join(_LUXARCH_TEST_DB_SCOPES)}"
        )
    return ""


def _luxarch_test_db_check_identity(
    dbapi_conn: object, record: object, *_: object, dialect: object = None
) -> None:
    """Ask a new driver connection where it is, before anything is sent on it; refuse it unless it
    is where this process may connect. The engine's own dialect decides whether it is Postgres's: a
    Postgres connection in a run whose database is not on Postgres is judged too."""
    if dialect is None:
        dialect = getattr(
            getattr(record, "_ConnectionRecord__pool", None), "_dialect", None
        )
    nothing = _luxarch_test_db_unscoped()
    if nothing:
        _luxarch_test_db_close(dbapi_conn)
        _luxarch_test_db_refuse(nothing)
    info = getattr(record, "info", None)
    if isinstance(info, dict) and info.get(_LUXARCH_TEST_DB_MARKER):
        return
    if _luxarch_test_db_other_dialect(dialect):
        # an engine SQLAlchemy knows is not Postgres's (a SQLite cache): no server to ask; in the
        # suite's own process luxarch_test_db.bound judges its file
        return
    if _luxarch_test_db_scope() == "server":
        # the migration tests' own databases: any database, on a server the run marked
        pass
    elif _luxarch_test_db_target() is None:
        # TEST_DATABASE_URL not set yet: nothing says which database is the run's
        _luxarch_test_db_close(dbapi_conn)
        _luxarch_test_db_refuse(
            "a connection opened before TEST_DATABASE_URL was set: set it before any engine "
            "connects (a raw_connection() or pool.connect() too)"
        )
    elif not _luxarch_test_db_is_postgres():
        if dialect is not None and _luxarch_test_db_pg(dialect):
            _luxarch_test_db_close(dbapi_conn)
            _luxarch_test_db_refuse(
                "a Postgres connection in a run whose TEST_DATABASE_URL is not on Postgres"
            )
        return
    try:
        row = _luxarch_test_db_ask_dbapi(dbapi_conn)
    except Exception as e:
        # the server's own words (a cancelled statement reads as one), on one line
        try:
            said = " ".join(str(e).split())[:300] or str(
                getattr(e, "sqlstate", None) or getattr(e, "pgcode", None) or ""
            )
        except Exception:
            said = ""  # an error that cannot print itself is refused all the same
        row = (
            f"{type(e).__name__} asking it: {said}"
            if said
            else f"{type(e).__name__} asking it"
        )
    # the suite's own engine on the maintenance database, while it creates a worker's
    allow = ("postgres",) if _LUXARCH_TEST_DB_CREATING else ()
    try:
        _luxarch_test_db_confirm(row, allow)
    except AssertionError:
        _luxarch_test_db_close(dbapi_conn)
        raise
    if isinstance(info, dict):
        info[_LUXARCH_TEST_DB_MARKER] = True


# The `do_connect` listeners that ask the question: this module's, and the one a test-db-bound block
# that carried everything itself registered.
_LUXARCH_TEST_DB_ASKERS = frozenset(
    {"_luxarch_test_db_check_do_connect", "_check_do_connect"}
)


def _luxarch_test_db_check_do_connect(
    dialect: object, record: object, cargs: object, cparams: object
) -> object:
    """The dialect opens the driver connection here and the server is asked on it, before the pool's
    `first_connect` (the dialect's own initialize, an app's one-time setup) sends anything, and
    outside the lock SQLAlchemy holds while `first_connect` runs: a question asked under that lock
    deadlocks two connections opened at once on one event loop."""
    nothing = _luxarch_test_db_unscoped()
    if nothing:
        # refused before the driver is called: no connection is opened at all
        _luxarch_test_db_refuse(nothing)
    connect = getattr(dialect, "connect", None)
    if not callable(connect) or not isinstance(cargs, (list, tuple)):
        return None
    # the rest of the chain first, as SQLAlchemy would run it (an app's listener that sets the
    # password or returns a connection of its own), then the dialect, then the question
    dbapi_conn = None
    for fn in getattr(getattr(dialect, "dispatch", None), "do_connect", ()):
        # this listener, another copy of it (imported under a second name), and an older block's
        # own (a conftest not yet re-emitted), which would call this one back
        if getattr(fn, "__name__", "") in _LUXARCH_TEST_DB_ASKERS:
            continue
        dbapi_conn = fn(dialect, record, cargs, cparams)
        if dbapi_conn is not None:
            break
    if dbapi_conn is None:
        dbapi_conn = connect(*cargs, **(cparams if isinstance(cparams, dict) else {}))
    _luxarch_test_db_check_identity(dbapi_conn, record, dialect=dialect)
    return dbapi_conn


_LUXARCH_TEST_DB_AUDITING: list[bool] = []
# What every process the run starts is handed with the check: the run's token, scope, fixed
# database and refusals directory, and where it keeps bytecode (never the checkout's). Each as this
# process last saw it, so an environment a test cleared or replaced is put back.
_LUXARCH_TEST_DB_CARRIED = (
    "LUXARCH_TEST_RUN",
    "LUXARCH_TEST_DB_SCOPE",
    "LUXARCH_TEST_DB_TARGET",
    "LUXARCH_TEST_DB_REFUSALS",
    "PYTHONPYCACHEPREFIX",
)
_LUXARCH_TEST_DB_SEEN: dict[str, str] = {}


def _luxarch_test_db_children() -> str:
    """This package's `children` directory, whose `sitecustomize` installs the check."""

    return os.path.join(os.path.dirname(os.path.realpath(__file__)), "children")


def _luxarch_test_db_with_children(path: str) -> str:
    """PYTHONPATH `path` with `children` first, and nowhere else."""

    here = _luxarch_test_db_children()
    rest = [p for p in path.split(os.pathsep) if p and os.path.realpath(p) != here]
    return os.pathsep.join([here, *rest])


def _luxarch_test_db_carried() -> dict[str, str]:
    """The settings a child is handed: PYTHONPATH with `children` first, and the run's own
    LUXARCH_TEST_* (as the environment holds them now, else as this process last saw them)."""

    out = {
        "PYTHONPATH": _luxarch_test_db_with_children(os.environ.get("PYTHONPATH", ""))
    }
    for name in _LUXARCH_TEST_DB_CARRIED:
        value = os.environ.get(name) or _LUXARCH_TEST_DB_SEEN.get(name, "")
        if value:
            _LUXARCH_TEST_DB_SEEN[name] = value
            out[name] = value
    if _LUXARCH_TEST_DB_SCOPE:
        out["LUXARCH_TEST_DB_SCOPE"] = _LUXARCH_TEST_DB_SCOPE[0]
    return out


def _luxarch_test_db_audit(event: str, args: tuple[object, ...]) -> None:
    """Hand every process the run starts the check, as it starts: a process started with no
    environment of its own inherits this one, which gets `children` first on its PYTHONPATH and the
    run's settings back; one `subprocess` starts with a dict environment of its own gets them put
    into that environment. Nothing is ever refused here: a test may change the environment as it
    likes (`mock.patch.dict(os.environ)`, `os.environ.clear()`)."""

    if event == "subprocess.Popen" and len(args) >= 4:
        env = args[3]
    elif event in ("os.posix_spawn", "os.exec") and len(args) >= 3:
        env = args[2]
    elif event == "os.system":
        env = None
    else:
        return
    carried = _luxarch_test_db_carried()
    # never let a repair stop a process from starting
    with contextlib.suppress(Exception):
        if env is None:
            for name, value in carried.items():
                if os.environ.get(name) != value:
                    os.environ[name] = value
        elif event == "subprocess.Popen" and isinstance(env, dict):
            as_bytes = any(isinstance(k, bytes) for k in env)
            for name, value in carried.items():
                if as_bytes:
                    old = env.get(os.fsencode(name), b"")
                    if name == "PYTHONPATH" and isinstance(old, bytes):
                        value = _luxarch_test_db_with_children(os.fsdecode(old))
                    env[os.fsencode(name)] = os.fsencode(value)
                else:
                    old = env.get(name, "")
                    if name == "PYTHONPATH" and isinstance(old, str):
                        value = _luxarch_test_db_with_children(old)
                    env[name] = value


def install() -> None:
    """Ask every SQLAlchemy connection the process opens from here on where it is. Installing twice
    adds nothing."""

    _luxarch_test_db_scope()
    # a process the run started holds to the database the run fixed, whatever a test re-pointed
    # TEST_DATABASE_URL to before starting it
    fixed = os.environ.get("LUXARCH_TEST_DB_TARGET") or os.environ.get(
        "TEST_DATABASE_URL"
    )
    if not _LUXARCH_TEST_DB_AT_IMPORT and fixed:
        _LUXARCH_TEST_DB_AT_IMPORT.append(fixed)
    if not event.contains(Engine, "do_connect", _luxarch_test_db_check_do_connect):
        event.listen(
            Engine, "do_connect", _luxarch_test_db_check_do_connect, insert=True
        )
    # `first_connect` runs before `connect` for a connection an engine's `creator=` opened: the
    # server is asked there first, before an app's own one-time setup sends anything
    for name in ("first_connect", "connect", "checkout"):
        if not event.contains(Pool, name, _luxarch_test_db_check_identity):
            event.listen(Pool, name, _luxarch_test_db_check_identity, insert=True)
    _luxarch_test_db_carried()
    if not _LUXARCH_TEST_DB_AUDITING:
        _LUXARCH_TEST_DB_AUDITING.append(True)
        sys.addaudithook(_luxarch_test_db_audit)


def refusals_directory() -> None:
    """Give the run a directory every process it starts records its refusals in (kept when the
    environment already names one: an xdist worker shares its controller's)."""

    if not os.environ.get("LUXARCH_TEST_DB_REFUSALS"):
        os.environ["LUXARCH_TEST_DB_REFUSALS"] = tempfile.mkdtemp(
            prefix="luxarch-test-db-refusals-"
        )
    if _LUXARCH_TEST_DB_AT_IMPORT and not os.environ.get("LUXARCH_TEST_DB_TARGET"):
        os.environ["LUXARCH_TEST_DB_TARGET"] = _LUXARCH_TEST_DB_AT_IMPORT[0]
    _luxarch_test_db_carried()


@runtime_checkable
class _LuxarchTestDbSession(Protocol):
    """What `report` uses of pytest's Session."""

    exitstatus: object
    config: object


def _luxarch_test_db_is_session(session: object) -> TypeGuard[_LuxarchTestDbSession]:
    """`session` is pytest's own Session, judged by its class (`_pytest.main.Session`) and never
    through the `pytest` module: a test may have dropped that from `sys.modules`, and this module,
    which `sitecustomize` loads into every process the run starts, never imports pytest."""
    return any(
        k.__module__ == "_pytest.main" and k.__qualname__ == "Session"
        for k in type(session).__mro__
    )


def report(session: object, problems: list[str]) -> None:
    """Fail the run on `problems`, through pytest's terminal reporter (standard error when it is off);
    an xdist worker hands them to the controller as its `shouldfail`, the one thing a worker's run
    reports that the controller stops on. The failure is recorded on the session before anything
    that can raise, the check that it is pytest's Session included (a test can rename that class):
    an xdist worker whose session-finish hook raises reports nothing to the controller, and its
    run would pass."""
    text = (
        "luxarch test-db-bound: FAIL — the suite is not on its throwaway database:\n  - "
        + "\n  - ".join(problems)
    )
    if problems and isinstance(session, _LuxarchTestDbSession):
        workeroutput = getattr(session.config, "workeroutput", None)
        if isinstance(workeroutput, dict):
            workeroutput["shouldfail"] = text
        session.exitstatus = 1
    if not _luxarch_test_db_is_session(session):
        raise AssertionError(
            f"luxarch test-db-bound: pytest handed {session!r}, not its Session"
        )
    if not problems:
        return
    manager = getattr(session.config, "pluginmanager", None)
    reporter = manager.get_plugin("terminalreporter") if manager is not None else None
    if reporter is not None:
        reporter.write_line("")
        reporter.write_line(text, red=True)
    else:
        sys.stderr.write(f"\n{text}\n")


def pytest_plugin_registered(plugin: object, manager: object) -> None:
    """Installed the moment pytest registers this module (`-p` is read before any conftest)."""
    if plugin is sys.modules.get(__name__):
        install()


def pytest_configure(config: object) -> None:
    refusals_directory()


def pytest_sessionfinish(session: object, exitstatus: object) -> None:
    """Every refusal of the run, this process's and its children's. Under luxarch_test_db.bound its
    own end-of-run check reports them, with the rest of what it judges."""
    config = getattr(session, "config", None)
    manager = getattr(config, "pluginmanager", None)
    if manager is not None and manager.has_plugin("luxarch_test_db.bound"):
        return
    report(
        session, sorted(set(_LUXARCH_TEST_DB_REFUSED) | set(_luxarch_test_db_logged()))
    )
