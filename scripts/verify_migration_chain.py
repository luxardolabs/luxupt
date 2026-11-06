# luxarch:migration-chain asset v5 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit migration-chain`.
# luxarch:migration-chain verifier v5 — DO NOT edit the marker line above (repo.migrations_build_schema finds it).
#
# WHAT THIS ANSWERS
# Two questions, one comparison (the database's schema against `Base.metadata`), failing on any
# difference in what it compares (listed below), additive included:
#
#   1) `python verify_migration_chain.py` (the `db-verify` target): does the migration chain BUILD what
#      the models describe, from EMPTY? Run it against a throwaway database just migrated to head. A
#      missing table shows as `add_table`, which the destructive-only `--emit schema-drift` passes. The
#      suite cannot answer this: the canonical harness builds its schema with `create_all`, so it never
#      runs a migration, and a chain broken on its first revision stays green until someone provisions
#      a fresh database.
#
#   2) `python verify_migration_chain.py --against [URL]` (the `db-drift` target): does a database the
#      chain has ALREADY stamped still match the models? A column altered by hand, or a migration edited
#      after it ran, leaves the stamp at head and the schema elsewhere; `alembic upgrade head` then does
#      nothing and only a query on the drifted column fails. With no URL, it reads DB_URL_ENV. It refuses
#      the database TEST_DATABASE_URL names: that one is built by `create_all` from the same metadata, so
#      it can only agree with itself.
#
# It only reads. Every connection runs inside a READ ONLY transaction (PostgreSQL) or with
# `PRAGMA query_only` on a file that must already exist (SQLite), so the server refuses any write; any
# other dialect is refused. Two server defaults compare first by PostgreSQL's plan of each, which runs
# neither unless a function is IMMUTABLE; only two the planner prints differently are evaluated, once, in
# that transaction, so two values that match today read as one default (`now() + '1 mon'` and
# `now() + '30 days'` in a 30-day month; `'1 day'` and `'24 hours'`, which `=` calls equal). It compares column types, nullability and server defaults, tables,
# indexes, unique and foreign-key constraints, and PostgreSQL enum types (their labels, in order), in
# the schemas alembic reads by default. It does not compare primary keys, CHECK or EXCLUDE constraints,
# a partial index's WHERE, generated-column expressions, or the labels of an enum inside an ARRAY or a
# TypeDecorator. Each drift prints as `table.column: db=<what the database has> model=<what the models
# say>`. Comment-only diffs are ignored. Exit 0: no drift; 1: drift; 2: refused (no URL, an unreachable
# database, an unsupported dialect, the test database, models with no tables, or any failure of its
# own), never a pass.
#
# HOW TO WIRE IT (FLEET-MAKEFILE-STANDARD, "the migration-chain gate")
#   1) Save as scripts/verify_migration_chain.py, where `luxarch --emit db-verify`'s targets run it.
#   2) Point METADATA_IMPORT at the package that imports EVERY model and DB_URL_ENV at the env var
#      holding the database URL.
#   3) Emit the db-verify block and add `db-verify` to `check`. luxarch gates that it is wired
#      (repo.migrations_build_schema); it never runs it (it has no database).
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy import Column, Connection, MetaData
    from sqlalchemy.engine import Dialect

# --- EDIT THIS ---------------------------------------------------------------------------------------
# The declarative Base, imported from the PACKAGE that registers every model (its `__init__` imports all
# model modules and re-exports Base). Do NOT point at a bare `base_model`: that Base's metadata reflects
# only whatever models happened to be imported by this script's import chain, so every unimported table
# reads as a false "remove_table" — the exact trap that nearly shipped a baseline missing three tables.
METADATA_IMPORT = "app.models:Base"
# The env var that holds the throwaway (freshly migrated) DB URL. Sync and async drivers both work.
DB_URL_ENV = "VERIFY_DATABASE_URL"
# -----------------------------------------------------------------------------------------------------

EXIT_DRIFT = 1
EXIT_REFUSED = 2
# A driver that cannot connect (DBAPIError aside, which is caught by name) or is not installed.
_UNREADABLE: tuple[type[Exception], ...] = (OSError, ImportError)
# METADATA_IMPORT names no module, no attribute, or something that is not a Base or MetaData.
_NAMED_LOAD_FAILURES: tuple[type[Exception], ...] = (
    ImportError,
    AttributeError,
    TypeError,
)
# The models' own import failed: `sys.exit` included, which would otherwise exit 0 with no comparison.
_ANY_LOAD_FAILURE: tuple[type[BaseException], ...] = (Exception, SystemExit)


class Refused(Exception):
    """The comparison cannot run, so it has no verdict: the caller exits EXIT_REFUSED."""


def _load(path: str) -> object:
    mod, _, attr = path.partition(":")
    if not attr:
        raise ValueError(f"expected 'module:attribute', got {path!r}")
    obj: object = importlib.import_module(mod)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _metadata(obj: object) -> MetaData:
    from sqlalchemy import MetaData

    found = getattr(
        obj, "metadata", obj
    )  # a declarative Base has .metadata; a MetaData is itself
    if not isinstance(found, MetaData):
        raise TypeError(
            f"METADATA_IMPORT={METADATA_IMPORT!r} is {type(found).__name__}, not a declarative "
            "Base or a MetaData"
        )
    return found


def _ops(diffs: Sequence[object]) -> list[tuple[object, ...]]:
    """compare_metadata returns op-tuples and LISTS of op-tuples (the modify_* ops); flatten both."""
    out: list[tuple[object, ...]] = []
    for d in diffs:
        if isinstance(d, list):
            out.extend(x for x in d if isinstance(x, tuple) and x)
        elif isinstance(d, tuple) and d:
            out.append(d)
    return out


def _type(t: object, dialect: Dialect) -> str:
    from sqlalchemy.types import TypeEngine

    if isinstance(t, TypeEngine):
        try:
            return str(t.compile(dialect=dialect))
        except Exception:  # a type this dialect cannot render still names itself
            return repr(t)
    return repr(t)


def _nullable(v: object) -> str:
    return "nullable" if v is True else "NOT NULL" if v is False else str(v)


def _model_sql(arg: object) -> str:
    """A model's server default as SQL, as DDL writes it: a string is a literal unless it is already
    quoted, and an expression compiles with no driver's doubled `%`."""
    from sqlalchemy.dialects.postgresql.base import PGDialect
    from sqlalchemy.sql import ClauseElement

    if isinstance(arg, ClauseElement):
        make_dialect: Callable[..., Dialect] = PGDialect
        named = make_dialect(paramstyle="named")
        return str(arg.compile(dialect=named, compile_kwargs={"literal_binds": True}))
    # `str.__str__`: a `class S(str, Enum)` member prints as `S.ACTIVE` but is the string `active`.
    text = str.__str__(arg) if isinstance(arg, str) else str(arg)
    return (
        text
        if text.startswith("'") and text.endswith("'")
        else "'" + text.replace("'", "''") + "'"
    )


def _default(d: object) -> str:
    from sqlalchemy.sql import ClauseElement

    if d is None:
        return "no default"
    arg = getattr(d, "arg", d)
    if isinstance(arg, ClauseElement):
        try:
            return f"default {_model_sql(arg)}"
        except Exception:  # a clause no dialect renders still names itself
            return f"default {arg}"
    return f"default {str.__str__(arg) if isinstance(arg, str) else arg}"


def _cols(obj: object) -> str:
    return ", ".join(str(getattr(c, "name", c)) for c in getattr(obj, "columns", ()))


def _named(obj: object) -> tuple[str, str]:
    table = getattr(obj, "table", None)
    return str(
        getattr(table, "name", table)
    ), f"{getattr(obj, 'name', None)}({_cols(obj)})"


def _renames(
    ops: list[tuple[object, ...]],
) -> tuple[list[str], list[tuple[object, ...]]]:
    """A constraint or index the database and the models both have on the same columns, under two
    names, as ONE line (`table.constraint(cols): db=<name> model=<name>`) instead of a remove and an
    add. The rest come back unpaired."""

    def key(op: tuple[object, ...]) -> tuple[str, str, str, object] | None:
        kind = str(op[0])
        if len(op) < 2 or not kind.startswith(("add_", "remove_")):
            return None
        what = kind.split("_", 1)[1]
        if what not in ("constraint", "index", "fk"):
            return None
        obj = op[1]
        table = getattr(getattr(obj, "table", None), "name", None)
        return (what, str(table), _cols(obj), getattr(obj, "unique", None))

    removed: dict[tuple[str, str, str, object], list[int]] = {}
    for i, op in enumerate(ops):
        k = key(op)
        if k is not None and str(op[0]).startswith("remove_"):
            removed.setdefault(k, []).append(i)
    paired: set[int] = set()
    lines: list[str] = []
    for i, op in enumerate(ops):
        k = key(op)
        if k is None or not str(op[0]).startswith("add_") or not removed.get(k):
            continue
        j = removed[k][0]
        if getattr(ops[j][1], "name", None) == getattr(op[1], "name", None):
            continue  # one name on both sides is a change to the object, not a rename
        removed[k].pop(0)
        paired.update((i, j))
        what, table, cols, _ = k
        lines.append(
            f"{table}.{what}({cols}): db={getattr(ops[j][1], 'name', None)} "
            f"model={getattr(op[1], 'name', None)}"
        )
    return lines, [op for i, op in enumerate(ops) if i not in paired]


def _describe(op: tuple[object, ...], dialect: Dialect) -> str:
    """One drift as `table.column: db=<x> model=<y>`."""
    kind = str(op[0])
    if kind in ("add_table", "remove_table"):
        name = getattr(op[1], "name", op[1])
        side = ("missing", "table") if kind == "add_table" else ("table", "missing")
        return f"{name}: db={side[0]} model={side[1]}"
    if kind in ("add_column", "remove_column") and len(op) >= 4:
        col = op[3]
        desc = _type(getattr(col, "type", None), dialect)
        if getattr(col, "nullable", True) is False:
            desc += " NOT NULL"
        side = ("missing", desc) if kind == "add_column" else (desc, "missing")
        return f"{op[2]}.{getattr(col, 'name', col)}: db={side[0]} model={side[1]}"
    if kind.startswith("modify_") and len(op) >= 7:
        where = f"{op[2]}.{op[3]}"
        db, model = op[5], op[6]
        if kind == "modify_type":
            return f"{where}: db={_type(db, dialect)} model={_type(model, dialect)}"
        if kind == "modify_nullable":
            return f"{where}: db={_nullable(db)} model={_nullable(model)}"
        if kind == "modify_default":
            return f"{where}: db={_default(db)} model={_default(model)}"
    if kind.startswith(("add_", "remove_")) and len(op) >= 2:
        what = kind.split("_", 1)[1]
        table, name = _named(op[1])
        side = ("missing", what) if kind.startswith("add_") else (what, "missing")
        return f"{table}.{what} {name}: db={side[0]} model={side[1]}"
    return f"{kind}: {op}"


def _enum_drift(conn: Connection, metadata: MetaData) -> list[str]:
    """PostgreSQL enum types: alembic's comparison does not read their labels."""
    from sqlalchemy import Enum, text

    if conn.dialect.name != "postgresql":
        return []
    want: dict[tuple[str | None, str], list[str]] = {}
    for table in metadata.tables.values():
        for col in table.columns:
            t = col.type
            if isinstance(t, Enum) and t.native_enum and t.name:
                want[(t.schema, t.name)] = list(t.enums)
    if not want:
        return []
    have: dict[tuple[str, str], list[str]] = {}
    rows = conn.execute(
        text(
            "SELECT n.nspname, t.typname, e.enumlabel FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid = t.oid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "ORDER BY n.nspname, t.typname, e.enumsortorder"
        )
    )
    for schema, name, label in rows:
        have.setdefault((str(schema), str(name)), []).append(str(label))
    path = [
        str(row[0])
        for row in conn.execute(text("SELECT unnest(current_schemas(false))"))
    ]

    def found(schema: str | None, name: str) -> list[str] | str:
        # An enum with no schema is the one the search path resolves: the first schema holding the name.
        for s in [schema] if schema else path:
            if (s, name) in have:
                return have[(s, name)]
        return "missing"

    return [
        f"enum {f'{schema}.' if schema else ''}{name}: db={found(schema, name)} model={labels}"
        for (schema, name), labels in sorted(
            want.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])
        )
        if found(schema, name) != labels
    ]


def _read_only(conn: Connection) -> None:
    """Open the connection's transaction READ ONLY, so the server refuses any write it is sent."""
    name = conn.dialect.name
    if name == "postgresql":
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout = '60s'")
        # A driver in autocommit ends that transaction at once, so the setting has to be read back.
        if conn.exec_driver_sql("SHOW transaction_read_only").scalar() != "on":
            raise Refused(
                "the connection is not read-only (is autocommit set on the URL?)"
            )
    elif name == "sqlite":
        conn.exec_driver_sql("PRAGMA query_only = ON")
    else:
        raise Refused(
            f"cannot open a read-only connection on {name!r}: only PostgreSQL and SQLite"
        )


def _plan_output(conn: Connection, sql: str, regtype: str) -> object:
    """What PostgreSQL's planner makes of a default as this column's type, read from the plan's own
    `Output` field: EXPLAIN runs no volatile function, no sequence and no write, and a server's
    `Query Identifier` row differs between equal expressions."""
    from sqlalchemy import text

    stmt = f"EXPLAIN (VERBOSE, COSTS OFF, FORMAT JSON) SELECT ({sql})::{regtype}"
    plan = conn.execute(text(stmt.replace(":", "\\:"))).scalar()
    # asyncpg hands the plan back as text, psycopg as parsed JSON.
    return json.loads(plan if isinstance(plan, str) else json.dumps(plan))[0]["Plan"][
        "Output"
    ]


def _same_value(conn: Connection, a: str, b: str, regtype: str) -> bool:
    """Two defaults the planner prints differently can still be one default (`timezone('utc', now())`
    and `now() AT TIME ZONE 'utc'`). Asked once, in the READ ONLY transaction: a volatile function never
    matches, a sequence refuses to advance, and a statement timeout ends a slow one."""
    from sqlalchemy import text

    stmt = f"SELECT ({a})::{regtype} IS NOT DISTINCT FROM ({b})::{regtype}"
    return conn.execute(text(stmt.replace(":", "\\:"))).scalar() is True


def _may_hold_statements(sql: str) -> bool:
    """A `;` is safe only inside a plain `'...'`, in text with no other kind of quoting or comment to
    misread. Fails closed: a `;` in an E-string or a dollar quote reads as drift, and is not sent."""
    if ";" not in sql:
        return False
    return bool(re.search(r"""[$"\\]|--|/\*""", sql)) or ";" in re.sub(
        r"'(?:[^']|'')*'", "", sql
    )


def _is_constant(output: object) -> bool:
    """One literal in a plan's Output: `'x'::text`, `0`, `true`, `NULL::date`."""
    return (
        isinstance(output, list)
        and len(output) == 1
        and bool(
            re.fullmatch(
                r"""\(?(?:'(?:[^']|'')*'|-?[0-9.]+|true|false|NULL)\)?(?:::.+)?""",
                str(output[0]),
            )
        )
    )


def _sqlstate(exc: Exception) -> str | None:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def _default_differs(
    conn: Connection,
    inspected: Column[object],
    model: Column[object],
    db_default: str | None,
) -> bool:
    """Alembic's own comparison runs `SELECT <db default> = <model default>`: a volatile default never
    equals itself, a type with no `=` (json) errors, and one error ends the run. Here the planner compares
    first, so those stay equal, and only what it prints differently is evaluated. The cast keeps a column's
    numeric and time precision but no text or bit length, because `::varchar(3)` truncates in silence.
    No default and a NULL default read the same."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    if model.primary_key and model is model.table.autoincrement_column:
        return False  # the column's sequence default, which alembic also leaves out
    arg = getattr(model.server_default, "arg", None)
    if model.server_default is not None and arg is None:
        return False  # FetchedValue: the models say the database owns this default
    wanted = _model_sql(arg) if arg is not None else None
    if db_default == wanted:
        return False
    if _may_hold_statements(wanted or ""):
        return True  # more than one statement is no default, and is not sent
    regtype = conn.execute(
        text(
            "SELECT format_type(a.atttypid, CASE WHEN a.atttypid = ANY (ARRAY["
            "'varchar'::regtype, 'bpchar'::regtype, 'bit'::regtype, 'varbit'::regtype, "
            "'varchar[]'::regtype, 'bpchar[]'::regtype, 'bit[]'::regtype, 'varbit[]'::regtype"
            "]) THEN -1 ELSE a.atttypmod END) FROM pg_attribute a "
            "WHERE a.attrelid = to_regclass(:t) AND a.attname = :c"
        ),
        {
            "t": conn.dialect.identifier_preparer.format_table(inspected.table),
            "c": inspected.name,
        },
    ).scalar()
    if regtype is None:
        return True
    db_sql, model_sql = db_default or "NULL", wanted or "NULL"
    try:
        with conn.begin_nested():
            db_plan = _plan_output(conn, db_sql, str(regtype))
            model_plan = _plan_output(conn, model_sql, str(regtype))
            if db_plan == model_plan:
                return False
            # A constant and an expression can match today (a frozen date and CURRENT_DATE) and no other day.
            if _is_constant(db_plan) != _is_constant(model_plan):
                return True
        with conn.begin_nested():
            return not _same_value(conn, db_sql, model_sql, str(regtype))
    except DBAPIError as exc:
        if _sqlstate(exc) == "42501":
            raise Refused(
                f"this role may not read the default of {inspected.table.name}.{inspected.name}"
            ) from exc
        return True  # a side PostgreSQL cannot plan or evaluate is not the database's default


def _use_planned_defaults() -> None:
    """Make `_default_differs` the PostgreSQL comparison. Alembic reads an impl class by its dialect name.
    Its `compare_server_default` callable option cannot be used: alembic raises AttributeError on an
    Identity or Computed column before calling it."""
    from alembic.ddl.postgresql import PostgresqlImpl

    def compare(
        impl: PostgresqlImpl,
        inspector_column: Column[object],
        metadata_column: Column[object],
        _rendered_metadata_default: str | None,
        rendered_inspector_default: str | None,
    ) -> bool:
        conn = impl.connection
        if conn is None:
            raise Refused("alembic gave the comparison no connection")
        return _default_differs(
            conn, inspector_column, metadata_column, rendered_inspector_default
        )

    type(
        "_PlannedDefaults",
        (PostgresqlImpl,),
        {"__dialect__": "postgresql", "compare_server_default": compare},
    )


def _compare_sync(conn: Connection, metadata: MetaData) -> list[str]:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    _read_only(conn)
    _use_planned_defaults()
    ctx = MigrationContext.configure(
        conn, opts={"compare_type": True, "compare_server_default": True}
    )
    ops = [
        o
        for o in _ops(list(compare_metadata(ctx, metadata)))
        if "comment" not in str(o[0])
    ]
    renamed, rest = _renames(ops)
    return (
        [_describe(o, conn.dialect) for o in rest]
        + renamed
        + _enum_drift(conn, metadata)
    )


def _diff(url: str, metadata: MetaData) -> list[str]:
    """Own the engine lifecycle end-to-end (no shared pool, no NullPool) — same reason as schema-drift.
    Nothing is committed: leaving `connect()` rolls the transaction back."""
    from sqlalchemy import make_url
    from sqlalchemy.exc import DBAPIError

    parsed = make_url(url)
    # Named by parts: a rendered URL still prints `?password=` from its query.
    backend = parsed.get_backend_name()
    name = parsed.database or ""
    if backend != "sqlite" and ("@" in name or "/" in name):
        name = "(unreadable)"  # an unencoded `@` or `/` puts part of the password here
    shown = f"{backend} database {name} on {(parsed.host or '').rsplit('@', 1)[-1]}"
    if (
        backend == "sqlite"
        and parsed.database not in (None, "", ":memory:")
        and not (parsed.database or "").startswith("file:")
        and not os.path.exists(parsed.database or "")
    ):
        raise Refused(f"cannot read {shown}: no such file")
    try:
        if make_url(url).get_dialect().is_async:
            import asyncio

            from sqlalchemy.ext.asyncio import create_async_engine

            async def _run() -> list[str]:
                engine = create_async_engine(url)
                try:
                    async with engine.connect() as conn:
                        return await conn.run_sync(lambda c: _compare_sync(c, metadata))
                finally:
                    await engine.dispose()

            return asyncio.run(_run())

        from sqlalchemy import create_engine

        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                return _compare_sync(conn, metadata)
        finally:
            engine.dispose()
    except DBAPIError as exc:
        raise _unreadable(shown, exc) from exc
    except _UNREADABLE as exc:
        raise _unreadable(shown, exc) from exc


def _unreadable(shown: str, exc: Exception) -> Refused:
    # Class names only: a driver's message can repeat the URL it was given, password included.
    orig = getattr(exc, "orig", None)
    cause = f" ({type(orig).__name__})" if orig is not None else ""
    return Refused(f"cannot read {shown}: {type(exc).__name__}{cause}")


def _same_database(a: str, b: str) -> bool:
    """Whether two URLs name one database, by what libpq reads: host and port from the URL or its query,
    any of several hosts, and the database from the path or `dbname`. Text alone cannot match an IP
    against the host name it belongs to."""
    from sqlalchemy import make_url

    def place(u: str) -> tuple[str, str, set[tuple[str, int | None]]]:
        x = make_url(u)
        backend = x.get_backend_name()
        if backend == "sqlite":
            return backend, os.path.abspath(x.database or ""), set()
        default = 5432 if backend == "postgresql" else None
        query_port = x.query.get("port")
        port = x.port or (
            int(str(query_port)) if isinstance(query_port, str) else default
        )
        spots: set[tuple[str, int | None]] = set()
        for value in (x.host, x.query.get("host"), x.query.get("hostaddr")):
            for entry in (value,) if isinstance(value, str) else (value or ()):
                for one in str(entry).split(","):
                    host, _, own_port = one.lower().partition(":")
                    if host:
                        spots.add((host, int(own_port) if own_port.isdigit() else port))
        return backend, str(x.database or x.query.get("dbname") or ""), spots

    x, y = place(a), place(b)
    return x[:2] == y[:2] and (x[0] == "sqlite" or bool(x[2] & y[2]))


def _url(against: str | None) -> str:
    url = against or os.environ.get(DB_URL_ENV, "")
    if not url:
        raise Refused(f"no database URL: pass --against <url> or set {DB_URL_ENV}")
    return url


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a database's schema with the models."
    )
    parser.add_argument(
        "--against",
        nargs="?",
        const="",
        default=None,
        metavar="URL",
        help=f"a database the chain already stamped (default: ${DB_URL_ENV})",
    )
    args = parser.parse_args(argv)
    live = args.against is not None
    try:
        url = _url(args.against)
        test_url = os.environ.get("TEST_DATABASE_URL", "")
        if live and test_url and _same_database(url, test_url):
            raise Refused(
                "that is the database TEST_DATABASE_URL names: create_all builds it from these "
                "same models, so it can only agree with them"
            )
        try:
            metadata = _metadata(_load(METADATA_IMPORT))
        except _NAMED_LOAD_FAILURES as exc:
            raise Refused(
                f"cannot load the models from METADATA_IMPORT={METADATA_IMPORT!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        # A settings error's text can hold the value it refused, so only its class is printed.
        except _ANY_LOAD_FAILURE as exc:
            raise Refused(
                f"cannot load the models from METADATA_IMPORT={METADATA_IMPORT!r}: "
                f"{type(exc).__name__}"
            ) from exc
        if not metadata.tables:
            raise Refused(
                f"METADATA_IMPORT={METADATA_IMPORT!r} holds no tables: its `__init__` must import "
                "every model"
            )
        drift = _diff(url, metadata)
    except Refused as exc:
        print(f"REFUSING: {exc}")
        return EXIT_REFUSED
    # A failure with no verdict is a refusal, never drift and never a pass.
    except Exception as exc:
        print(f"REFUSING: the comparison failed: {type(exc).__name__}")
        return EXIT_REFUSED
    tables = len(metadata.tables)
    what = (
        "the live database"
        if live
        else "the database `alembic upgrade head` built from empty"
    )
    print(f"tables in metadata: {tables}")
    print(f"structural drift between {what} and the models: {len(drift)}")
    if drift:
        print()
        for line in drift:
            print(f"  {line}")
        if live:
            print(
                "\nThe live database does not match the models, and `alembic upgrade head` changes "
                "nothing on a database already at head. Write a migration that moves the database to "
                "the models; do not edit the models to match a hand-made schema."
            )
        else:
            print(
                "\nThe migration chain does not build what the models describe (a missing table is an "
                "ADDITIVE add_table, which schema-drift passes). Write the migration; do NOT edit the "
                "models to match a schema the chain cannot produce."
            )
        return EXIT_DRIFT
    print(f"✓ {what} matches the models ({tables} tables, 0 drift)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
