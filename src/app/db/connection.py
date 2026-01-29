"""SQLite async database connection and session management."""

import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated

import config
from fastapi import Depends
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

# Database path - separate from output to allow local SSD for DB, NFS for images
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
DATABASE_DIR = Path(os.getenv("DATABASE_DIR", str(OUTPUT_DIR)))
DATABASE_PATH = DATABASE_DIR / "timelapse.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH}"

# Create async engine with SQLite-specific settings
# QueuePool: keeps connections alive so SQLite page cache stays warm across requests.
# Combined with mmap, the entire DB lives in process memory — reads are memory reads, not syscalls.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
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


# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides a database session with automatic commit/rollback."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Type alias for dependency injection
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def init_db() -> None:
    """Initialize database tables."""
    from logging_config import get_logger

    logger = get_logger(__name__)

    # Import all models to register them with SQLAlchemy metadata
    # This ensures all tables are created
    from models import (  # noqa: F401
        Activity,
        BackupSettings,
        Camera,
        Capture,
        FetchSettings,
        Job,
        SchedulerSettings,
        Timelapse,
        User,
    )

    from db.base import Base

    DATABASE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        async with engine.begin() as conn:
            # PRAGMAs (WAL, synchronous, busy_timeout, mmap, cache) are set automatically
            # by the _set_sqlite_pragmas event listener on every new connection.
            logger.info("SQLite WAL mode enabled for better concurrency")

            # Enable incremental auto-vacuum if not already set.
            # auto_vacuum mode can only change on an empty DB or via VACUUM after setting it.
            result = await conn.execute(text("PRAGMA auto_vacuum"))
            current = result.scalar()
            if current != 2:  # 2 = INCREMENTAL
                logger.info("Enabling incremental auto-vacuum (requires VACUUM)", extra={"current_mode": current})
                await conn.execute(text("PRAGMA auto_vacuum = INCREMENTAL"))
                await conn.execute(text("VACUUM"))
                logger.info("Incremental auto-vacuum enabled")

            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        # Handle race condition where another process might have created tables
        if "already exists" in str(e).lower():
            logger.debug("Tables already exist (likely created by another service)", extra={"error": str(e)})
        else:
            raise


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
