# luxarch:migration-chain asset v4 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit migration-chain`.
# luxarch:migration-chain verifier v4 — DO NOT edit the marker line above (repo.migrations_build_schema finds it).
#
# WHAT THIS ANSWERS
# Two questions, one comparison (the database's schema against `Base.metadata`), failing on ANY
# structural difference, additive included:
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
# `PRAGMA query_only` (SQLite), so the server refuses any write; any other dialect is refused. It
# compares column types, nullability and server defaults, tables, indexes, unique and foreign-key
# constraints, and PostgreSQL enum types (their labels, in order). Each drift prints as
# `table.column: db=<what the database has> model=<what the models say>`. Comment-only diffs are
# ignored. Exit 0: no drift; 1: drift; 2: refused (no URL, an unreachable database, an unsupported
# dialect, the test database), never a pass.
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
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Connection, MetaData
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
_UNLOADABLE: tuple[type[Exception], ...] = (
    ImportError,
    AttributeError,
    TypeError,
    ValueError,
)


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


def _default(d: object) -> str:
    if d is None:
        return "no default"
    arg = getattr(d, "arg", d)
    return f"default {getattr(arg, 'text', arg)}"


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
        j = removed[k].pop(0)
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
    want: dict[str, list[str]] = {}
    for table in metadata.tables.values():
        for col in table.columns:
            t = col.type
            if isinstance(t, Enum) and t.native_enum and t.name:
                want[t.name] = list(t.enums)
    if not want:
        return []
    have: dict[str, list[str]] = {}
    rows = conn.execute(
        text(
            "SELECT t.typname, e.enumlabel FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid = t.oid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = ANY (current_schemas(false)) "
            "ORDER BY t.typname, e.enumsortorder"
        )
    )
    for name, label in rows:
        have.setdefault(str(name), []).append(str(label))
    return [
        f"enum {name}: db={have.get(name, 'missing')} model={labels}"
        for name, labels in sorted(want.items())
        if have.get(name) != labels
    ]


def _read_only(conn: Connection) -> None:
    """Open the connection's transaction READ ONLY, so the server refuses any write it is sent."""
    name = conn.dialect.name
    if name == "postgresql":
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
    elif name == "sqlite":
        conn.exec_driver_sql("PRAGMA query_only = ON")
    else:
        raise Refused(
            f"cannot open a read-only connection on {name!r}: only PostgreSQL and SQLite"
        )


def _compare_sync(conn: Connection, metadata: MetaData) -> list[str]:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    _read_only(conn)
    ctx = MigrationContext.configure(
        conn, opts={"compare_type": True, "compare_server_default": True}
    )
    ops = [
        o
        for o in _ops(list(compare_metadata(ctx, metadata)))
        if not str(o[0]).startswith("modify_comment")
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

    shown = make_url(url).render_as_string(hide_password=True)
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
    lines = str(getattr(exc, "orig", exc)).strip().splitlines()
    return Refused(
        f"cannot read {shown}: {type(exc).__name__}: {lines[0] if lines else ''}"
    )


def _same_database(a: str, b: str) -> bool:
    from sqlalchemy import make_url

    x, y = make_url(a), make_url(b)
    return (x.get_backend_name(), x.host, x.port, x.database) == (
        y.get_backend_name(),
        y.host,
        y.port,
        y.database,
    )


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
        except _UNLOADABLE as exc:
            raise Refused(
                f"cannot load the models from METADATA_IMPORT={METADATA_IMPORT!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        drift = _diff(url, metadata)
    except Refused as exc:
        print(f"REFUSING: {exc}")
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
                "\nThe live database does not match the models, while its alembic stamp says it is "
                "current, so `alembic upgrade head` will not change it. Write a migration that moves "
                "the database to the models; do not edit the models to match a hand-made schema."
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
