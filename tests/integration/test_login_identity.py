"""One login per stripped spelling: `bob `, `\\tbob` and `bob` are the same login.

Logins are stored stripped, the database refuses a second row with the same stripped login, and
every lookup by login (sign-in, the session check, the recovery CLI) matches the stripped login.
An integer path parameter too large for SQLite is a 422, never a 500.
"""

import sys
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, literal, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.user_crud import user_crud
from app.models.user_model import LOGIN_WHITESPACE
from app.services.core.admin_bootstrap_core_service import AdminBootstrapCoreService
from app.web.auth import COOKIE_NAME, AuthService
from app.web.main import app

PASSWORD = "a-long-enough-password"
WHITESPACE = [" ", "\t", "\n", "\r", "\xa0", "\u3000", "\u2028"]
# Not whitespace, but sharing UTF-8 lead or continuation bytes with characters that are.
NEAR_MISSES = ["\u2010", "\u3001", "\u1681", "\xc2", "\x80", "\xe0", "\u205e", "\u2027"]


@pytest_asyncio.fixture
async def admin_client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    await user_crud.create_user(
        db, username="owner-i", password=PASSWORD, is_admin=True
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        resp = await ac.post(
            "/login", data={"username": "owner-i", "password": PASSWORD}
        )
        assert resp.status_code in (302, 303)
        yield ac


def test_the_login_whitespace_is_exactly_what_python_strips() -> None:
    every = "".join(chr(i) for i in range(sys.maxunicode + 1) if chr(i).isspace())
    assert set(LOGIN_WHITESPACE) == set(every)


async def test_sqlite_trim_strips_exactly_what_str_strip_strips(
    db: AsyncSession,
) -> None:
    shapes: list[str] = []
    for ch in LOGIN_WHITESPACE:
        shapes += [ch + "bob", "bob" + ch, ch * 3 + "bob" + ch * 2, "b" + ch + "ob"]
    for near in NEAR_MISSES:
        shapes += [near + "bob" + near, " " + near + "bob" + near + "\u3000"]

    trimmed = [
        await db.scalar(select(func.trim(literal(s), LOGIN_WHITESPACE))) for s in shapes
    ]

    assert [
        (s, t) for s, t in zip(shapes, trimmed, strict=True) if t != s.strip()
    ] == []


@pytest.mark.parametrize("ws", WHITESPACE)
async def test_the_database_refuses_a_second_row_with_the_same_stripped_login(
    db: AsyncSession, ws: str
) -> None:
    await user_crud.create_user(db, username="bob", password=PASSWORD)

    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO users (username, password_hash, is_admin, session_version) "
                    "VALUES (:u, 'x', 0, 1)"
                ),
                {"u": f"{ws}bob{ws}"},
            )


@pytest.mark.parametrize("ws", WHITESPACE)
async def test_a_login_is_stored_stripped(db: AsyncSession, ws: str) -> None:
    user = await user_crud.create_user(db, username=f"{ws}dave{ws}", password=PASSWORD)
    assert user.username == "dave"


async def test_an_update_stores_the_login_stripped_and_keeps_its_sessions(
    db: AsyncSession,
) -> None:
    user = await user_crud.create_user(db, username="erin", password=PASSWORD)
    version = user.session_version

    await user_crud.update_user(db, user, username="\terin ")

    assert user.username == "erin"
    assert user.session_version == version


async def test_an_update_that_changes_the_login_ends_its_sessions(
    db: AsyncSession,
) -> None:
    user = await user_crud.create_user(db, username="erin", password=PASSWORD)
    version = user.session_version

    await user_crud.update_user(db, user, username=" erina ")

    assert user.username == "erina"
    assert user.session_version != version


@pytest.mark.parametrize("typed", ["bob ", "\tbob", "\u3000bob\n"])
async def test_sign_in_with_surrounding_whitespace_finds_the_login(
    db: AsyncSession, client: AsyncClient, typed: str
) -> None:
    await user_crud.create_user(db, username="bob", password=PASSWORD)

    resp = await client.post("/login", data={"username": typed, "password": PASSWORD})

    assert resp.status_code in (302, 303)
    assert client.cookies


async def test_sign_in_still_refuses_a_different_login(
    db: AsyncSession, client: AsyncClient
) -> None:
    await user_crud.create_user(db, username="bob", password=PASSWORD)
    resp = await client.post("/login", data={"username": "bobby", "password": PASSWORD})
    assert resp.status_code == 400


async def test_a_session_issued_to_the_unstripped_login_is_kept(
    db: AsyncSession,
) -> None:
    """A token issued before the stored login was stripped names it with its whitespace."""
    dave = await user_crud.create_user(db, username="dave", password=PASSWORD)
    token = AuthService.create_access_token(
        data={"sub": "dave ", "sv": dave.session_version}
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="https://test", cookies={COOKIE_NAME: token}
    ) as client:
        resp = await client.get("/system/users", follow_redirects=False)

    assert resp.status_code == 200


async def test_the_recovery_cli_finds_the_login_typed_with_whitespace(
    db: AsyncSession,
) -> None:
    await user_crud.create_user(db, username="bob", password=PASSWORD)

    temporary = await AdminBootstrapCoreService().reset_password(db, " bob\t")

    assert temporary


TOO_BIG = str(2**63)
PATH_PROBES = [
    "/system/partials/user-delete-confirm/{n}",
    "/timelapses/{n}/lightbox",
    "/timelapses/{n}/video",
    "/timelapses/{n}/thumbnail",
    "/images/lightbox/somecam/{n}",
    "/images/lightbox/somecam/{n}/content",
    "/images/file/somecam/60/{n}",
    "/images/file/somecam/{n}/1700000000",
]


@pytest.mark.parametrize("probe", PATH_PROBES)
async def test_an_integer_path_value_too_large_to_store_is_a_422(
    admin_client: AsyncClient, probe: str
) -> None:
    resp = await admin_client.get(probe.format(n=TOO_BIG))
    assert resp.status_code == 422


@pytest.mark.parametrize("probe", PATH_PROBES)
async def test_a_small_integer_path_value_is_no_500(
    admin_client: AsyncClient, probe: str
) -> None:
    resp = await admin_client.get(probe.format(n=1))
    assert resp.status_code < 500
