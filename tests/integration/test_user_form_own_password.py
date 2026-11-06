"""An admin changes their own password only through the form that checks the current one.

The user form sets a password with no current password. For another user that is an admin's job. For
the admin's own account it would let a stolen session take the account over, past the lockout on
`/account/password`.
"""

from fastapi import Request
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.user_crud import user_crud
from app.models.user_model import User
from app.services.core.user_core_service import UserCoreService
from app.services.views.users_view_service import UsersViewService
from app.web.main import app

PASSWORD = "a-long-enough-password"
NEW_PASSWORD = "another-long-password-2"


async def _signed_in_admin(client: AsyncClient, db: AsyncSession) -> User:
    admin = await user_crud.create_user(
        db, username="owner", password=PASSWORD, is_admin=True
    )
    resp = await client.post("/login", data={"username": "owner", "password": PASSWORD})
    assert resp.status_code in (302, 303)
    return admin


async def _password_works(client: AsyncClient, username: str, password: str) -> bool:
    client.cookies.clear()
    resp = await client.post(
        "/login", data={"username": username, "password": password}
    )
    return resp.status_code in (302, 303)


async def test_an_admin_cannot_set_their_own_password_on_the_user_form(
    client: AsyncClient, db: AsyncSession
) -> None:
    admin = await _signed_in_admin(client, db)

    resp = await client.post(
        f"/system/users/{admin.id}",
        data={
            "username": "owner",
            "password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
            "is_admin": "true",
        },
    )

    assert resp.status_code == 400
    assert "/account/password" in resp.text
    assert await _password_works(client, "owner", PASSWORD)
    assert not await _password_works(client, "owner", NEW_PASSWORD)


def _view_for_session(
    db: AsyncSession, session_user_id: int | None
) -> UsersViewService:
    state = {} if session_user_id is None else {"session_user_id": session_user_id}
    request = Request({"type": "http", "app": app, "state": state})
    return UsersViewService(UserCoreService(db), request)


async def test_your_row_stays_yours_after_your_name_moves_to_another_row(
    db: AsyncSession,
) -> None:
    """The session is checked before the route's transaction, so by the time this runs another
    request can have renamed the caller's row and given their old name to a different one."""
    mine = await user_crud.create_user(
        db, username="temp", password=PASSWORD, is_admin=True
    )
    theirs = await user_crud.create_user(db, username="owner", password=PASSWORD)
    view = _view_for_session(db, mine.id)

    refused = await view.validate_user_update(
        mine.id, "temp", NEW_PASSWORD, NEW_PASSWORD
    )
    allowed = await view.validate_user_update(
        theirs.id, "owner", NEW_PASSWORD, NEW_PASSWORD
    )

    assert refused == ["Change your own password at /account/password"]
    assert allowed == []


async def test_a_request_with_no_session_id_cannot_set_a_password(
    db: AsyncSession,
) -> None:
    user = await user_crud.create_user(db, username="second", password=PASSWORD)
    view = _view_for_session(db, None)

    errors = await view.validate_user_update(
        user.id, "second", NEW_PASSWORD, NEW_PASSWORD
    )

    assert errors == ["Change your own password at /account/password"]


async def test_a_blank_password_with_a_confirmation_does_not_report_success(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _signed_in_admin(client, db)
    other = await user_crud.create_user(db, username="second", password=PASSWORD)

    resp = await client.post(
        f"/system/users/{other.id}",
        data={"username": "second", "confirm_password": NEW_PASSWORD},
    )

    assert resp.status_code == 400
    assert "Passwords do not match" in resp.text


async def test_your_own_edit_form_links_to_the_account_page_instead_of_offering_a_password(
    client: AsyncClient, db: AsyncSession
) -> None:
    admin = await _signed_in_admin(client, db)

    resp = await client.get(f"/system/partials/user-form?user_id={admin.id}")

    assert resp.status_code == 200
    assert 'name="password"' not in resp.text
    assert 'href="/account/password"' in resp.text


async def test_another_users_edit_form_still_offers_a_password(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _signed_in_admin(client, db)
    other = await user_crud.create_user(db, username="second", password=PASSWORD)

    resp = await client.get(f"/system/partials/user-form?user_id={other.id}")

    assert resp.status_code == 200
    assert 'name="password"' in resp.text


async def test_an_admin_can_still_edit_their_own_account_without_a_password(
    client: AsyncClient, db: AsyncSession
) -> None:
    admin = await _signed_in_admin(client, db)

    resp = await client.post(
        f"/system/users/{admin.id}",
        data={"username": "owner-renamed", "is_admin": "true"},
    )

    assert resp.status_code < 400
    assert await _password_works(client, "owner-renamed", PASSWORD)


async def test_an_admin_can_still_set_another_users_password(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _signed_in_admin(client, db)
    other = await user_crud.create_user(db, username="second", password=PASSWORD)

    resp = await client.post(
        f"/system/users/{other.id}",
        data={
            "username": "second",
            "password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )

    assert resp.status_code < 400
    assert await _password_works(client, "second", NEW_PASSWORD)
