"""The engine, the two session owners (get_db / get_db_context) and schema bootstrap."""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.core import config
from app.crud.backup_settings_crud import backup_settings_crud
from app.crud.fetch_settings_crud import fetch_settings_crud
from app.crud.scheduler_settings_crud import scheduler_settings_crud

logger = logging.getLogger(__name__)

# Database path - separate from output to allow local SSD for DB, NFS for images
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
DATABASE_DIR = Path(os.getenv("DATABASE_DIR", str(OUTPUT_DIR)))
DATABASE_PATH = DATABASE_DIR / "timelapse.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH}"

# Create async engine with SQLite-specific settings
# QueuePool: keeps connections alive so SQLite page cache stays warm across requests.
# Combined with mmap, the entire DB lives in process memory — reads are memory reads, not syscalls.
# hide_parameters: a database error's message and traceback never carry the bound values, which
# include the Protect API key and password, so an error can be logged in full.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    hide_parameters=True,
    poolclass=AsyncAdaptedQueuePool,
    pool_size=config.DATABASE_POOL_SIZE,
    max_overflow=config.DATABASE_POOL_MAX_OVERFLOW,
    connect_args={
        "check_same_thread": False,
        "timeout": config.DATABASE_TIMEOUT,
    },
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
    """Set SQLite PRAGMAs on every new connection.

    PRAGMAs like synchronous, busy_timeout, mmap_size, and cache_size are
    per-connection settings that reset to defaults on new connections. This
    listener ensures every pooled connection gets the right settings.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute(f"PRAGMA busy_timeout={config.DATABASE_BUSY_TIMEOUT}")
    cursor.execute(f"PRAGMA mmap_size={config.DATABASE_MMAP_SIZE}")
    cursor.execute(f"PRAGMA cache_size=-{config.DATABASE_CACHE_SIZE_KB}")
    cursor.close()
    # SQLAlchemy's pysqlite/aiosqlite transaction recipe, part 1: the driver stops managing
    # transactions itself. Left to it, the driver emits BEGIN only before the first INSERT/UPDATE/
    # DELETE, so a transaction's reads run outside it and a SAVEPOINT is not nested in anything.
    dbapi_connection.isolation_level = None


@event.listens_for(engine.sync_engine, "begin")
def _begin_transaction(conn: Connection) -> None:
    """Recipe part 2: SQLAlchemy's transaction begins the SQLite transaction, reads included.

    Each transaction is then one SQLite transaction from its first statement, and a nested
    `begin_nested()` is a real SAVEPOINT inside it (the test harness's per-test rollback relies on
    that). It begins IMMEDIATE, taking the write lock up front: a DEFERRED transaction that reads,
    then writes after another connection (the fetch worker) committed, fails at once with
    "database is locked", and busy_timeout cannot retry that. IMMEDIATE waits for the lock
    instead. An autocommit connection (init_db's VACUUM) gets no BEGIN at all.
    """
    if conn.get_execution_options().get("isolation_level") == "AUTOCOMMIT":
        return
    conn.exec_driver_sql("BEGIN IMMEDIATE")


# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Dependency that provides a database session with automatic commit/rollback."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_context() -> AsyncIterator[AsyncSession]:
    """The NON-request transaction owner: commits on clean exit, rolls back on exception.

    The twin of get_db for code that has no request to hang off -- background jobs, the
    scheduler, the CLI. These are the ONLY two session owners; everything else (crud,
    services, routers) is transaction-agnostic and is HANDED a session.

    Never open `async_session()` directly outside this module: a raw factory session in a
    background path never commits (the work silently rolls back at close) and takes a second
    pooled connection while the caller may already hold one -- a deadlock under load
    (fw.session_minted_in_owner).
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _run_migrations() -> None:
    """Run Alembic migrations to upgrade the database schema.

    This ensures existing databases get schema updates when upgrading to new versions.
    Migrations are idempotent - they check if changes are needed before applying. env.py
    configures the fleet logging module (idempotent), so the app's loggers survive the run.
    """
    # Get path to alembic.ini relative to this file
    app_dir = Path(__file__).parent.parent
    alembic_ini = app_dir / "alembic.ini"

    if not alembic_ini.exists():
        logger.warning(
            "alembic.ini not found, skipping migrations",
            extra={"path": str(alembic_ini)},
        )
        return

    alembic_cfg = AlembicConfig(str(alembic_ini))
    # Override the script location to be absolute
    alembic_cfg.set_main_option("script_location", str(app_dir / "db" / "migrations"))
    # Name the database explicitly: env.py honours a caller-supplied URL, so the engine
    # and the migration can never disagree about which file they mean.
    alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL)

    # The migration chain IS the schema (fw.schema_from_migrations) -- there is no
    # create_all fallback, so a failed upgrade must stop startup, never be logged past.
    logger.info("Running database migrations")
    command.upgrade(alembic_cfg, "head")
    logger.info("Database migrations complete")


async def init_db() -> None:
    """Initialize database tables."""
    # Off the event loop: a blocking pathlib call in an async def stalls EVERY concurrent
    # request on this worker (ruff ASYNC240). asyncio.to_thread is the asyncio fix --
    # ruff's message suggests trio/anyio, which does not apply to this fleet.
    await asyncio.to_thread(DATABASE_DIR.mkdir, parents=True, exist_ok=True)

    try:
        # Autocommit: VACUUM cannot run inside a transaction (_begin_transaction emits none here).
        async with engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
            # PRAGMAs (WAL, synchronous, busy_timeout, mmap, cache) are set automatically
            # by the _set_sqlite_pragmas event listener on every new connection.
            logger.info("SQLite WAL mode enabled for better concurrency")

            # Enable incremental auto-vacuum if not already set.
            # auto_vacuum mode can only change on an empty DB or via VACUUM after setting it.
            result = await conn.execute(text("PRAGMA auto_vacuum"))
            current = result.scalar()
            if current != 2:  # 2 = INCREMENTAL
                logger.info(
                    "Enabling incremental auto-vacuum (requires VACUUM)",
                    extra={"current_mode": current},
                )
                await conn.execute(text("PRAGMA auto_vacuum = INCREMENTAL"))
                await conn.execute(text("VACUUM"))
                logger.info("Incremental auto-vacuum enabled")

        # Build/upgrade the schema from the migration chain -- the ONE construction, the same
        # on a fresh deploy and an existing database (fw.schema_from_migrations).
        _run_migrations()

        await seed_singleton_settings()
    except Exception as e:
        # Handle race condition where another process might have created tables
        if "already exists" in str(e).lower():
            logger.debug(
                "Tables already exist (likely created by another service)",
                extra={"error": str(e)},
            )
        else:
            raise


async def seed_singleton_settings() -> None:
    """Create the singleton settings rows once, at startup, if they are absent.

    The settings tables each hold exactly one row (id=1). Creating it lazily on READ meant
    every page render reached a DB write, which under SameSite=Lax is CSRF-reachable
    (fw.state_changing_get): a link the victim clicks arrives with the session cookie
    attached. Seeding here keeps the read path genuinely read-only -- the writer owns the
    write. Idempotent: an existing row is left untouched.
    """
    async with get_db_context() as db:
        await fetch_settings_crud.seed_default(db)
        await scheduler_settings_crud.seed_default(db)
        await backup_settings_crud.seed_default(db)


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
