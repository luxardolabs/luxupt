"""A sign-in verifies the password against a fresh read of the user row.

The session the login runs in may already hold the user (the auth dependency loads it). When a reset
changes the stored hash underneath that object, the verify must see the stored value, not the copy
in the session: the old password is refused and the new one signs in.
"""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.user_crud import user_crud
from app.models.user_model import User
from app.utils.password_hash import hash_password

OLD_PASSWORD = "the-old-password-123"
NEW_PASSWORD = "the-new-password-456"


async def test_a_sign_in_verifies_against_the_stored_hash_not_the_session_copy(
    db: AsyncSession,
) -> None:
    user = await user_crud.create_user(db, username="resetme", password=OLD_PASSWORD)
    held = await user_crud.get_by_username(db, "resetme")
    assert held is user

    await db.execute(
        update(User)
        .where(User.id == user.id)
        .values(password_hash=hash_password(NEW_PASSWORD))
        .execution_options(synchronize_session=False)
    )

    assert await user_crud.authenticate(db, "resetme", OLD_PASSWORD) is None
    signed_in = await user_crud.authenticate(db, "resetme", NEW_PASSWORD)
    assert signed_in is not None
    assert signed_in.id == user.id
