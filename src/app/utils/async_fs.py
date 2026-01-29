"""Async wrappers for blocking filesystem operations."""

import asyncio
import os
from pathlib import Path


async def path_exists(p: Path) -> bool:
    """Check if a path exists without blocking the event loop."""
    return await asyncio.to_thread(p.exists)


async def path_stat(p: Path) -> os.stat_result:
    """Get file stat information without blocking the event loop."""
    return await asyncio.to_thread(p.stat)


async def path_unlink(p: Path, missing_ok: bool = False) -> None:
    """Delete a file without blocking the event loop."""
    await asyncio.to_thread(p.unlink, missing_ok)


async def path_mkdir(p: Path, parents: bool = False, exist_ok: bool = False) -> None:
    """Create a directory without blocking the event loop."""
    await asyncio.to_thread(p.mkdir, parents=parents, exist_ok=exist_ok)


async def path_glob(p: Path, pattern: str) -> list[Path]:
    """Glob a directory pattern without blocking the event loop."""
    return await asyncio.to_thread(lambda: list(p.glob(pattern)))


async def makedirs(path: str, exist_ok: bool = False) -> None:
    """Create directories recursively without blocking the event loop."""
    await asyncio.to_thread(os.makedirs, path, exist_ok=exist_ok)


async def write_file_bytes(path: str, data: bytes) -> None:
    """Write binary data to a file without blocking the event loop."""

    def _write() -> None:
        """Write bytes to disk."""
        with open(path, "wb") as f:
            f.write(data)

    await asyncio.to_thread(_write)


async def file_exists_and_size(path: str) -> tuple[bool, int]:
    """Check existence and get size in one thread dispatch."""

    def _check() -> tuple[bool, int]:
        """Return (exists, size) for the given path."""
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return True, os.path.getsize(path)
        return False, 0

    return await asyncio.to_thread(_check)
