"""The admin user form holds a new or renamed login to the policy `/setup` enforces.

A look-alike login (a zero-width space inside `admin`) renders the same as the real one in the user
list and the logs. A login stored before the policy stays editable as long as it is not renamed.
"""

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.user_crud import user_crud
from app.services.core.user_core_service import UserCoreService
from app.services.views.users_view_service import UsersViewService
from app.web.main import app

PASSWORD = "a-long-enough-password"
LOOK_ALIKE = "ad​min"


def _view(db: AsyncSession, session_user_id: int) -> UsersViewService:
    request = Request(
        {"type": "http", "app": app, "state": {"session_user_id": session_user_id}}
    )
    return UsersViewService(UserCoreService(db), request)


async def test_a_new_look_alike_login_is_refused(db: AsyncSession) -> None:
    admin = await user_crud.create_user(
        db, username="owner", password=PASSWORD, is_admin=True
    )
    view = _view(db, admin.id)

    refused = await view.validate_user_create(LOOK_ALIKE, PASSWORD, PASSWORD)
    allowed = await view.validate_user_create("camera-op", PASSWORD, PASSWORD)

    assert refused == [
        "Username refused: the login is not a username "
        "(letters, digits, '.', '_' or '-', up to 64)"
    ]
    assert allowed == []


async def test_a_rename_to_a_look_alike_is_refused(db: AsyncSession) -> None:
    admin = await user_crud.create_user(
        db, username="owner", password=PASSWORD, is_admin=True
    )
    other = await user_crud.create_user(db, username="second", password=PASSWORD)
    view = _view(db, admin.id)

    errors = await view.validate_user_update(other.id, LOOK_ALIKE, "", "")

    assert len(errors) == 1
    assert errors[0].startswith("Username refused:")


async def test_a_stored_login_outside_the_policy_stays_editable(
    db: AsyncSession,
) -> None:
    admin = await user_crud.create_user(
        db, username="owner", password=PASSWORD, is_admin=True
    )
    legacy = await user_crud.create_user(db, username="old name", password=PASSWORD)
    view = _view(db, admin.id)

    errors = await view.validate_user_update(legacy.id, "old name", "", "")

    assert errors == []
