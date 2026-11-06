"""A password set counts session_version from the stored value, not the one its row was loaded with.

Each stale-row test loads a row, then moves the stored version behind the ORM's back. A route cannot
hold such a row, because it reads and writes under one BEGIN IMMEDIATE transaction: these tests hold
the crud layer to the same answer without that lock.
"""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud.admin_bootstrap_crud import set_password
from app.crud.user_crud import user_crud
from app.db.database import engine
from app.models.user_model import User
from app.utils.password_hash import hash_password, verify_password
from app.web.main import app

OLD_PASSWORD = "an-old-long-enough-password"
NEW_PASSWORD = "a-long-enough-new-password"
STORED_VERSION = 5


@pytest.fixture
async def stale_user(db: AsyncSession) -> User:
    user = await user_crud.create_user(db, username="stale", password=OLD_PASSWORD)
    assert user.session_version == 0
    await db.execute(
        update(User)
        .where(User.id == user.id)
        .values(session_version=STORED_VERSION)
        .execution_options(synchronize_session=False)
    )
    return user


async def _stored_version(db: AsyncSession, user_id: int) -> int | None:
    return await db.scalar(select(User.session_version).where(User.id == user_id))


async def test_update_user_counts_from_the_stored_version(
    db: AsyncSession, stale_user: User
) -> None:
    updated = await user_crud.update_user(db, stale_user, password=NEW_PASSWORD)

    assert await _stored_version(db, stale_user.id) == STORED_VERSION + 1
    assert updated.session_version == STORED_VERSION + 1
    assert verify_password(NEW_PASSWORD, updated.password_hash)


async def test_set_password_counts_from_the_stored_version(
    db: AsyncSession, stale_user: User
) -> None:
    await set_password(
        db, stale_user, hash_password(NEW_PASSWORD), must_change_password=True
    )

    assert await _stored_version(db, stale_user.id) == STORED_VERSION + 1
    assert stale_user.session_version == STORED_VERSION + 1
    assert verify_password(NEW_PASSWORD, stale_user.password_hash)
    assert stale_user.must_change_password


async def test_update_without_a_password_keeps_sessions(
    db: AsyncSession, stale_user: User
) -> None:
    updated = await user_crud.update_user(db, stale_user, is_admin=False)

    assert await _stored_version(db, stale_user.id) == STORED_VERSION
    assert updated.session_version == STORED_VERSION


def _refuse_the_version_write(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    if statement.startswith("UPDATE users") and "session_version" in statement:
        raise OperationalError(statement, parameters, Exception("injected"))


@pytest.fixture
def version_write_refused() -> Iterator[Callable[[], None]]:
    def arm() -> None:
        event.listen(
            engine.sync_engine, "before_cursor_execute", _refuse_the_version_write
        )

    yield arm
    if event.contains(
        engine.sync_engine, "before_cursor_execute", _refuse_the_version_write
    ):
        event.remove(
            engine.sync_engine, "before_cursor_execute", _refuse_the_version_write
        )


async def test_an_admin_set_password_and_its_session_end_are_stored_together(
    durable_db: async_sessionmaker[AsyncSession],
    version_write_refused: Callable[[], None],
) -> None:
    async with durable_db() as session:
        await user_crud.create_user(session, username="admin", password=OLD_PASSWORD)
        target = await user_crud.create_user(
            session, username="target", password=OLD_PASSWORD, is_admin=False
        )
        await session.commit()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as admin:
        await admin.post("/login", data={"username": "admin", "password": OLD_PASSWORD})
        version_write_refused()
        await admin.post(
            f"/system/users/{target.id}",
            data={
                "username": "target",
                "password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
        )

    async with durable_db() as session:
        stored = await user_crud.get_by_username(session, "target")
    assert stored is not None
    assert stored.session_version == 0
    assert verify_password(OLD_PASSWORD, stored.password_hash)
