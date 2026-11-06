"""A session token fits only the user it was issued to, and an ended session stays ended.

A token is `{sub: username, sv: session_version}`. Each session_version is random, and a new password
or name replaces it, so a sequence of edits cannot line a row up with an old token.
"""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud.admin_bootstrap_crud import set_password
from app.crud.user_crud import user_crud
from app.db.database import engine
from app.models.user_model import User, new_session_version
from app.utils.password_hash import hash_password, verify_password
from app.web.auth import COOKIE_NAME
from app.web.main import app

OLD_PASSWORD = "an-old-long-enough-password"
NEW_PASSWORD = "a-long-enough-new-password"
SMALL_VERSION = 5
# A version above this is drawn at random, not stepped up from a small one. A random 62-bit value
# lands at or below it about once in a billion draws.
NO_WALK_REACHES = 2**32


@pytest.fixture
async def small_version_user(db: AsyncSession) -> User:
    user = await user_crud.create_user(db, username="small", password=OLD_PASSWORD)
    user.session_version = SMALL_VERSION
    await db.flush()
    return user


async def test_update_user_with_a_password_replaces_the_version_unpredictably(
    db: AsyncSession, small_version_user: User
) -> None:
    updated = await user_crud.update_user(db, small_version_user, password=NEW_PASSWORD)

    assert updated.session_version > NO_WALK_REACHES
    assert verify_password(NEW_PASSWORD, updated.password_hash)


async def test_update_user_with_a_new_name_replaces_the_version_unpredictably(
    db: AsyncSession, small_version_user: User
) -> None:
    updated = await user_crud.update_user(db, small_version_user, username="renamed")

    assert updated.session_version > NO_WALK_REACHES


async def test_set_password_replaces_the_version_unpredictably(
    db: AsyncSession, small_version_user: User
) -> None:
    await set_password(
        db, small_version_user, hash_password(NEW_PASSWORD), must_change_password=True
    )

    assert small_version_user.session_version > NO_WALK_REACHES
    assert verify_password(NEW_PASSWORD, small_version_user.password_hash)
    assert small_version_user.must_change_password
    assert small_version_user.updated_at is not None


async def test_update_without_a_password_keeps_sessions(
    db: AsyncSession, small_version_user: User
) -> None:
    updated = await user_crud.update_user(db, small_version_user, is_admin=False)

    assert updated.session_version == SMALL_VERSION


async def test_a_password_it_cannot_hash_changes_nothing(
    db: AsyncSession, small_version_user: User
) -> None:
    with pytest.raises(UnicodeEncodeError):
        await user_crud.update_user(
            db, small_version_user, username="renamed", password="\udc80"
        )

    assert small_version_user.username == "small"
    assert small_version_user.session_version == SMALL_VERSION


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
        signed_in = await admin.post(
            "/login", data={"username": "admin", "password": OLD_PASSWORD}
        )
        assert signed_in.status_code in (302, 303)
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
    assert stored.session_version == target.session_version
    assert verify_password(OLD_PASSWORD, stored.password_hash)


def _browser(cookie: str | None = None) -> AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    browser = AsyncClient(transport=transport, base_url="https://test")
    if cookie is not None:
        browser.cookies.set(COOKIE_NAME, cookie)
    return browser


async def _sign_in(username: str) -> str:
    async with _browser() as browser:
        response = await browser.post(
            "/login", data={"username": username, "password": OLD_PASSWORD}
        )
        assert response.status_code in (302, 303)
        cookie = browser.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return cookie


async def _status(cookie: str, path: str) -> tuple[int, str]:
    async with _browser(cookie) as browser:
        response = await browser.get(path)
    return response.status_code, response.headers.get("location", "")


async def _edit(admin_cookie: str, user_id: int, **form: str) -> None:
    async with _browser(admin_cookie) as browser:
        response = await browser.post(f"/system/users/{user_id}", data=form)
    assert response.status_code == 200


async def _seed(
    durable_db: async_sessionmaker[AsyncSession], username: str, *, is_admin: bool
) -> int:
    async with durable_db() as session:
        user = await user_crud.create_user(
            session, username=username, password=OLD_PASSWORD, is_admin=is_admin
        )
        await session.commit()
        return user.id


async def test_new_users_do_not_start_at_the_same_version(db: AsyncSession) -> None:
    first = await user_crud.create_user(db, username="first", password=OLD_PASSWORD)
    second = await user_crud.create_user(db, username="second", password=OLD_PASSWORD)
    assert first.session_version != second.session_version


async def test_a_deleted_users_cookie_does_not_open_a_new_user_of_that_name(
    durable_db: async_sessionmaker[AsyncSession],
) -> None:
    target_id = await _seed(durable_db, "alice", is_admin=False)
    old_cookie = await _sign_in("alice")
    assert (await _status(old_cookie, "/system/users"))[0] != 200

    async with durable_db() as session:
        row = await user_crud.get(session, target_id)
        assert row is not None
        await session.delete(row)
        await session.commit()
    await _seed(durable_db, "alice", is_admin=True)

    assert await _sign_in("alice")
    status, location = await _status(old_cookie, "/system/users")
    assert (status, location.split("?")[0]) == (302, "/login")


async def test_renaming_away_and_back_does_not_revive_a_session(
    durable_db: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(durable_db, "admin", is_admin=True)
    target_id = await _seed(durable_db, "alice", is_admin=False)
    admin_cookie = await _sign_in("admin")
    old_cookie = await _sign_in("alice")
    assert (await _status(old_cookie, "/cameras"))[0] == 200

    await _edit(admin_cookie, target_id, username="alicia")
    await _edit(admin_cookie, target_id, username="alice")

    status, location = await _status(old_cookie, "/cameras")
    assert (status, location.split("?")[0]) == (302, "/login")
    assert await _sign_in("alice")


async def test_saving_the_form_with_the_same_name_keeps_the_session(
    durable_db: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(durable_db, "admin", is_admin=True)
    target_id = await _seed(durable_db, "alice", is_admin=False)
    admin_cookie = await _sign_in("admin")
    cookie = await _sign_in("alice")

    await _edit(admin_cookie, target_id, username="alice")

    assert (await _status(cookie, "/cameras"))[0] == 200


def test_versions_are_drawn_far_apart() -> None:
    assert abs(new_session_version() - new_session_version()) > NO_WALK_REACHES


async def test_each_password_set_draws_a_new_version(
    db: AsyncSession, small_version_user: User
) -> None:
    await user_crud.update_user(db, small_version_user, password=NEW_PASSWORD)
    after_admin_set = small_version_user.session_version
    await user_crud.update_user(db, small_version_user, password=OLD_PASSWORD)
    after_second_admin_set = small_version_user.session_version
    await set_password(
        db, small_version_user, hash_password(NEW_PASSWORD), must_change_password=True
    )
    after_reset = small_version_user.session_version
    await set_password(
        db, small_version_user, hash_password(OLD_PASSWORD), must_change_password=False
    )

    assert (
        len(
            {
                after_admin_set,
                after_second_admin_set,
                after_reset,
                small_version_user.session_version,
            }
        )
        == 4
    )


@pytest.mark.parametrize("offset", [-1, 1])
async def test_a_cookie_fits_only_its_exact_version(
    durable_db: async_sessionmaker[AsyncSession], offset: int
) -> None:
    target_id = await _seed(durable_db, "alice", is_admin=False)
    cookie = await _sign_in("alice")
    assert (await _status(cookie, "/cameras"))[0] == 200

    async with durable_db() as session:
        await session.execute(
            update(User)
            .where(User.id == target_id)
            .values(session_version=User.session_version + offset)
        )
        await session.commit()

    status, location = await _status(cookie, "/cameras")
    assert (status, location.split("?")[0]) == (302, "/login")


async def test_a_second_rename_round_trip_does_not_revive_a_session(
    durable_db: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(durable_db, "admin", is_admin=True)
    target_id = await _seed(durable_db, "alice", is_admin=False)
    admin_cookie = await _sign_in("admin")
    await _edit(admin_cookie, target_id, username="alicia")
    await _edit(admin_cookie, target_id, username="alice")
    second_cookie = await _sign_in("alice")

    await _edit(admin_cookie, target_id, username="alicia")
    await _edit(admin_cookie, target_id, username="alice")

    status, location = await _status(second_cookie, "/cameras")
    assert (status, location.split("?")[0]) == (302, "/login")


async def test_a_case_only_rename_round_trip_does_not_revive_a_session(
    durable_db: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(durable_db, "admin", is_admin=True)
    target_id = await _seed(durable_db, "Alice", is_admin=False)
    admin_cookie = await _sign_in("admin")
    old_cookie = await _sign_in("Alice")

    await _edit(admin_cookie, target_id, username="alice")
    await _edit(admin_cookie, target_id, username="Alice")

    status, location = await _status(old_cookie, "/cameras")
    assert (status, location.split("?")[0]) == (302, "/login")


async def test_a_row_one_below_a_cookie_does_not_step_onto_it(
    durable_db: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(durable_db, "admin", is_admin=True)
    low_id = await _seed(durable_db, "low", is_admin=False)
    high_id = await _seed(durable_db, "high", is_admin=True)
    async with durable_db() as session:
        await session.execute(
            update(User).where(User.id == low_id).values(session_version=1)
        )
        await session.execute(
            update(User).where(User.id == high_id).values(session_version=0)
        )
        await session.commit()
    admin_cookie = await _sign_in("admin")
    low_cookie = await _sign_in("low")

    await _edit(admin_cookie, low_id, username="tmp", is_admin="false")
    await _edit(admin_cookie, high_id, username="low", is_admin="true")

    status, location = await _status(low_cookie, "/system/users")
    assert (status, location.split("?")[0]) == (302, "/login")
