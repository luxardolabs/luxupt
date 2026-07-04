"""Database backup service for periodic SQLite backups."""

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

from crud.backup_settings_crud import backup_settings_crud
from db.connection import DATABASE_PATH, OUTPUT_DIR, async_session
from logging_config import get_logger
from utils import async_fs

logger = get_logger(__name__)


class BackupService:
    """Service for periodic database backups with retention management."""

    def __init__(self) -> None:
        self.running = False
        self._task: asyncio.Task | None = None
        logger.info("BackupService initialized")

    async def start(self) -> None:
        """Start the backup service loop."""
        self.running = True
        logger.info("BackupService started")

        while self.running:
            try:
                # Load settings from database
                async with async_session() as session:
                    settings = await backup_settings_crud.get_settings(session)

                if not settings.enabled:
                    # Check again in 60 seconds
                    await asyncio.sleep(60)
                    continue

                # Perform backup
                backup_path = await self._create_backup(settings.backup_dir)

                if backup_path:
                    # Prune old backups
                    await self._prune_backups(settings.backup_dir, settings.retention)

                # Wait for next interval
                await asyncio.sleep(settings.interval)

            except asyncio.CancelledError:
                logger.info("BackupService cancelled")
                break
            except Exception as e:
                logger.error("BackupService error", extra={"error": str(e)})
                # Wait before retrying on error
                await asyncio.sleep(60)

    async def stop(self) -> None:
        """Stop the backup service."""
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("BackupService stopped")

    async def _create_backup(self, backup_dir: str) -> Path | None:
        """Create a database backup using SQLite's backup API.

        Returns the backup path on success, None on failure.
        """
        # Build backup directory path (relative to OUTPUT_DIR)
        backup_path = OUTPUT_DIR / backup_dir
        await async_fs.path_mkdir(backup_path, parents=True, exist_ok=True)

        # Generate backup filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_path / f"timelapse_{timestamp}.db"

        try:
            # Use SQLite's backup API (hot backup, works while DB is in use)
            # Run in thread pool to not block event loop
            await asyncio.to_thread(self._backup_sync, str(DATABASE_PATH), str(backup_file))

            # Get file size for logging
            stat_result = await async_fs.path_stat(backup_file)
            file_size = stat_result.st_size
            file_size_mb = round(file_size / 1024 / 1024, 2)

            logger.info(
                "Database backup created",
                extra={"path": str(backup_file), "size_mb": file_size_mb},
            )
            return backup_file

        except Exception as e:
            logger.error("Database backup failed", extra={"error": str(e)})
            return None

    def _backup_sync(self, source_path: str, dest_path: str) -> None:
        """Synchronous SQLite backup using the backup API."""
        source = sqlite3.connect(source_path)
        dest = sqlite3.connect(dest_path)

        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()

    def _prune_backups_sync(self, backup_dir: str, retention: int) -> None:
        """Synchronous prune logic — runs in thread pool."""
        backup_path = OUTPUT_DIR / backup_dir
        if not backup_path.exists():
            return

        backups = sorted(
            backup_path.glob("timelapse_*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for backup_file in backups[retention:]:
            try:
                backup_file.unlink()
                logger.info("Deleted old backup", extra={"path": str(backup_file)})
            except Exception as e:
                logger.warning("Failed to delete backup", extra={"path": str(backup_file), "error": str(e)})

    async def _prune_backups(self, backup_dir: str, retention: int) -> None:
        """Delete old backups beyond retention limit."""
        await asyncio.to_thread(self._prune_backups_sync, backup_dir, retention)

    async def backup_now(self) -> Path | None:
        """Trigger an immediate backup (for manual/API use).

        Returns the backup path on success, None on failure.
        """
        async with async_session() as session:
            settings = await backup_settings_crud.get_settings(session)

        backup_path = await self._create_backup(settings.backup_dir)

        if backup_path and settings.retention > 0:
            await self._prune_backups(settings.backup_dir, settings.retention)

        return backup_path
