"""A session is only as good as the user row behind it.

Found by the adversarial pass on the admin-bootstrap change: the cookie's JWT was checked for its
signature only, so it outlived a password reset, a password change, a deleted or renamed user, and
an emptied users table (which let an old cookie create the first admin without the /setup token).
The forced password change applied at sign-in only, and nothing checked is_admin. Each test here
failed on that code.
"""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.core.admin_bootstrap_core_service as core_mod
from app.crud.user_crud import user_crud
from app.services.core.admin_bootstrap_core_service import AdminBootstrapCoreService
from app.web.auth import COOKIE_NAME
from app.web.main import app

PASSWORD = "a-long-enough-password"
NEW_PASSWORD = "another-long-password-2"


@pytest.fixture(autouse=True)
def _token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_mod, "SETUP_TOKEN_FILE", tmp_path / "admin-setup-token")


async def _login(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post(
        "/login", data={"username": username, "password": password}
    )
    assert resp.status_code in (302, 303), resp.text[:300]
    return resp.headers["location"]


def _client_with(cookie: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://test")
    c.cookies.set(COOKIE_NAME, cookie)
    return c


def _new_user(username: str) -> dict[str, str]:
    return {"username": username, "password": PASSWORD, "confirm_password": PASSWORD}


async def test_control_a_live_session_works(
    client: AsyncClient, db: AsyncSession
) -> None:
    await user_crud.create_user(db, username="owner", password=PASSWORD)
    await _login(client, "owner", PASSWORD)
    assert (await client.get("/system/users")).status_code == 200


async def test_session_dies_after_reset_password(
    client: AsyncClient, db: AsyncSession
) -> None:
    await user_crud.create_user(db, username="owner", password=PASSWORD)
    await _login(client, "owner", PASSWORD)
    stolen = client.cookies.get(COOKIE_NAME)
    assert stolen

    await AdminBootstrapCoreService().reset_password(db, "owner")

    async with _client_with(stolen) as attacker:
        assert (await attacker.get("/system/users")).status_code == 302


async def test_a_refused_new_password_says_nothing_about_a_guessed_current_one(
    client: AsyncClient, db: AsyncSession
) -> None:
    await user_crud.create_user(db, username="owner", password=PASSWORD)
    await _login(client, "owner", PASSWORD)

    statuses = [
        (
            await client.post(
                "/account/password",
                data={
                    "current_password": guess,
                    "new_password": "x",
                    "confirm_password": "x",
                },
            )
        ).status_code
        for guess in ("a-wrong-guess", PASSWORD)
    ]

    assert statuses[0] == statuses[1]


async def test_session_dies_after_own_password_change(
    client: AsyncClient, db: AsyncSession
) -> None:
    await user_crud.create_user(db, username="owner", password=PASSWORD)
    await _login(client, "owner", PASSWORD)
    stolen = client.cookies.get(COOKIE_NAME)
    assert stolen

    resp = await client.post(
        "/account/password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?password_changed=1"

    async with _client_with(stolen) as attacker:
        assert (await attacker.get("/system/users")).status_code == 302
    assert await _login(client, "owner", NEW_PASSWORD) == "/cameras"


async def test_deleted_user_session_is_dead(
    client: AsyncClient, db: AsyncSession
) -> None:
    await user_crud.create_user(db, username="keeper", password=PASSWORD)
    leaver = await user_crud.create_user(db, username="leaver", password=PASSWORD)
    await _login(client, "leaver", PASSWORD)
    await db.delete(leaver)
    await db.flush()

    await client.post("/system/users", data=_new_user("intruder"))

    assert await user_crud.get_by_username(db, "intruder") is None


async def test_an_old_session_cannot_skip_the_setup_token(
    client: AsyncClient, db: AsyncSession
) -> None:
    # A persistent WEB_SESSION_SECRET over a users table that is empty again (restore, new
    # volume): /setup, with its token, must stay the only way to create the first admin.
    old = await user_crud.create_user(db, username="old", password=PASSWORD)
    await _login(client, "old", PASSWORD)
    await db.delete(old)
    await db.flush()

    await client.post("/system/users", data=_new_user("intruder"))

    assert await user_crud.get_by_username(db, "intruder") is None


async def test_forced_change_holds_every_page(
    client: AsyncClient, db: AsyncSession
) -> None:
    await user_crud.create_user(
        db, username="owner", password=PASSWORD, must_change_password=True
    )
    await _login(client, "owner", PASSWORD)

    for path in ("/cameras", "/system/users"):
        resp = await client.get(path)
        assert resp.status_code == 302, path
        assert resp.headers["location"] == "/account/password"
    htmx = await client.get("/system/partials/users", headers={"HX-Request": "true"})
    assert htmx.headers.get("HX-Redirect") == "/account/password"
    assert (await client.get("/account/password")).status_code == 200


async def test_a_non_admin_cannot_manage_users(
    client: AsyncClient, db: AsyncSession
) -> None:
    await user_crud.create_user(
        db, username="viewer", password=PASSWORD, is_admin=False
    )
    await _login(client, "viewer", PASSWORD)

    resp = await client.post(
        "/system/users", data={**_new_user("escalated"), "is_admin": "true"}
    )

    assert resp.status_code == 403
    assert await user_crud.get_by_username(db, "escalated") is None
    assert (await client.get("/system/users")).status_code == 403


async def test_an_unticked_admin_box_creates_a_non_admin(
    auth_client: AsyncClient, db: AsyncSession
) -> None:
    await auth_client.post("/system/users", data=_new_user("plain"))
    plain = await user_crud.get_by_username(db, "plain")
    assert plain is not None
    assert not plain.is_admin


async def test_the_last_admin_cannot_be_demoted_or_deleted(
    auth_client: AsyncClient, db: AsyncSession
) -> None:
    other = await user_crud.create_user(
        db, username="other", password=PASSWORD, is_admin=False
    )
    me = await user_crud.get_by_username(db, "testadmin")
    assert me is not None

    demote = await auth_client.post(
        f"/system/users/{me.id}", data={"username": "testadmin"}
    )
    assert "last administrator" in demote.text
    delete = await auth_client.delete(f"/system/users/{me.id}")
    assert "last administrator" in delete.text
    await db.refresh(me)
    assert me.is_admin
    assert await user_crud.get(db, other.id) is not None


async def test_a_renamed_user_signs_in_again(
    auth_client: AsyncClient, db: AsyncSession
) -> None:
    me = await user_crud.get_by_username(db, "testadmin")
    assert me is not None
    await auth_client.post(
        f"/system/users/{me.id}", data={"username": "renamed", "is_admin": "true"}
    )

    # The cookie named the old username: it ends, rather than reaching pages as nobody.
    assert (await auth_client.get("/account/password")).status_code == 302
    assert await _login(auth_client, "renamed", "test-password-123") == "/cameras"


async def test_a_limited_form_is_not_answered_with_the_login_page(
    auth_client: AsyncClient,
) -> None:
    form = {
        "current_password": "wrong-password-0000",
        "new_password": NEW_PASSWORD,
        "confirm_password": NEW_PASSWORD,
    }
    statuses = [
        (await auth_client.post("/account/password", data=form)).status_code
        for _ in range(8)
    ]
    assert 429 in statuses
    limited = await auth_client.post("/account/password", data=form)
    assert limited.status_code == 429
    assert "login" not in limited.text.lower()
