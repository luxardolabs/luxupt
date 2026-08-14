"""Durability-lock scaffolding proof (db-mutations playbook §7, ADR-003).

Two things are proven here, and everything in the DB-session-unification sweep rests on
them:

1. The ``durable_db`` harness — a real commit is visible from a SEPARATE connection, and a
   rolled-back write is not. This is the primitive every site lock uses to prove a write
   survives (or doesn't survive) its transaction.
2. The ``after_commit`` helper — the queued side effect fires exactly once, only after the
   session's transaction commits, and never on rollback.

These are themselves bite-proven: the assertions fail if the mechanism regresses (e.g. the
helper registers on the wrong hook, or the harness reads the owner's own uncommitted session).
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import activity_crud
from app.db.post_commit import after_commit
from app.models.activity_model import Activity
from app.models.enum_model import ActivityType

Maker = async_sessionmaker[AsyncSession]


# --- the harness: commit is durable across connections; rollback leaves nothing ---


async def test_committed_row_is_visible_from_a_separate_connection(
    durable_db: Maker,
) -> None:
    async with durable_db() as owner:
        row = await activity_crud.log(
            owner, activity_type=ActivityType.SERVICE_STARTED, message="durable-yes"
        )
        await owner.commit()
        pk = row.id
    # A fresh connection (only committed data is cross-connection visible).
    async with durable_db() as reader:
        seen = await reader.get(Activity, pk)
        assert seen is not None
        assert seen.message == "durable-yes"


async def test_rolled_back_row_is_not_visible(durable_db: Maker) -> None:
    async with durable_db() as owner:
        row = await activity_crud.log(
            owner, activity_type=ActivityType.SERVICE_STARTED, message="durable-no"
        )
        pk = row.id
        await owner.rollback()
    async with durable_db() as reader:
        assert await reader.get(Activity, pk) is None


# --- the after_commit helper: fires once on commit, never on rollback ---


async def test_after_commit_fires_once_after_commit(durable_db: Maker) -> None:
    fired: list[str] = []
    async with durable_db() as session:
        after_commit(session, lambda: fired.append("x"))
        await activity_crud.log(
            session, activity_type=ActivityType.SERVICE_STARTED, message="m"
        )
        assert fired == []  # not before the commit
        await session.commit()
    assert fired == ["x"]  # exactly once, after the commit


async def test_after_commit_skipped_on_rollback(durable_db: Maker) -> None:
    fired: list[str] = []
    async with durable_db() as session:
        after_commit(session, lambda: fired.append("x"))
        await activity_crud.log(
            session, activity_type=ActivityType.SERVICE_STARTED, message="m"
        )
        await session.rollback()
    assert fired == []  # the irreversible effect never runs on rollback
