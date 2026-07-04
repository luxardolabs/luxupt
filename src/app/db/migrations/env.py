"""Alembic migration environment for async SQLAlchemy."""

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Add src/app to path for imports
sys.path.insert(0, str(Path(__file__).parents[2]))

# Import all models to ensure they're registered with Base.metadata
from models import (  # noqa: F401, E402
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

from db.base import Base  # noqa: E402

# Alembic Config object
config = context.config

# Configure logging from alembic.ini
# disable_existing_loggers=False prevents Alembic from killing app loggers.
# Without this, fileConfig sets disabled=True on every existing logger,
# silently dropping ALL application logs after migrations run.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# SQLAlchemy metadata for autogenerate support
target_metadata = Base.metadata

# Database path configuration (same as connection.py)
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
DATABASE_DIR = Path(os.getenv("DATABASE_DIR", str(OUTPUT_DIR)))
DATABASE_PATH = DATABASE_DIR / "timelapse.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # Required for SQLite ALTER TABLE support
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # Required for SQLite ALTER TABLE support
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = DATABASE_URL

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Handles both standalone CLI usage and being called from within
    an async context (like during FastAPI startup).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop - we're in standalone CLI mode
        asyncio.run(run_async_migrations())
    else:
        # Already in an async context - create a new thread to run migrations
        # This avoids the "cannot call asyncio.run() while another loop is running" error
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, run_async_migrations())
            future.result()  # Wait for completion and propagate any exceptions


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
