"""Connection-level database maintenance helpers.

Raw SQL lives here by design: these are infrastructure statements (connectivity
ping, SQLite page reclamation), not business queries — those belong in crud/.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ping(db: AsyncSession) -> None:
    """Cheap connectivity check (SELECT 1). Raises on failure."""
    result = await db.execute(text("SELECT 1"))
    result.fetchone()


async def incremental_vacuum(db: AsyncSession, pages: int = 1000) -> None:
    """Reclaim freed SQLite pages in small increments (requires auto_vacuum=INCREMENTAL)."""
    await db.execute(text(f"PRAGMA incremental_vacuum({int(pages)})"))
