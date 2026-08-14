"""Typed helpers for Core DML (UPDATE / DELETE) execution."""

from sqlalchemy import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable


async def execute_rowcount(db: AsyncSession, stmt: Executable) -> int:
    """Execute a Core DML statement and return the number of affected rows.

    ``AsyncSession.execute`` is typed to return the base ``Result``, which does not
    expose ``rowcount``; a DML statement always yields a ``CursorResult`` at runtime.
    Narrow that once, here, so callers get a clean ``int`` without a per-site cast.
    """
    result = await db.execute(stmt)
    if isinstance(result, CursorResult):
        rowcount = result.rowcount
        return rowcount if rowcount is not None else 0
    return 0
