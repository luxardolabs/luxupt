"""User service for user management operations."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.user_crud import user_crud
from app.models.user_model import User
from app.schemas.user_schema import UserRead


class UserCoreService:
    """Service for user management operations."""

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    async def get_all(self) -> list[UserRead]:
        """Get all users ordered by username."""
        return [UserRead.model_validate(u) for u in await user_crud.get_all(self.db)]

    async def get_by_id(self, user_id: int) -> UserRead | None:
        """Get a user by ID."""
        user = await user_crud.get(self.db, user_id)
        return UserRead.model_validate(user) if user is not None else None

    async def get_by_username(self, username: str) -> User | None:
        """Get a user by username."""
        return await user_crud.get_by_username(self.db, username)

    async def create(
        self, username: str, password: str, is_admin: bool = True
    ) -> UserRead:
        """Create a user on an admin-assigned password, which they must change at first sign-in."""
        user = await user_crud.create_user(
            self.db,
            username=username,
            password=password,
            is_admin=is_admin,
            must_change_password=True,
        )
        return UserRead.model_validate(user)

    async def update(
        self,
        user_id: int,
        *,
        username: str | None = None,
        password: str | None = None,
        is_admin: bool | None = None,
    ) -> UserRead | None:
        """Update an existing user.

        A password set here is an admin's reset: temporary, so the user must change it at the next
        sign-in (FLEET-ADMIN-BOOTSTRAP-STANDARD §5). A user changes their own at /account/password.
        """
        user = await user_crud.get(self.db, user_id)
        if not user:
            return None

        updated = await user_crud.update_user(
            self.db,
            user,
            username=username,
            password=password,
            is_admin=is_admin,
            must_change_password=True if password is not None else None,
        )
        return UserRead.model_validate(updated)

    async def delete(self, user_id: int) -> bool:
        """Delete a user by ID. Returns True if deleted, False if not found."""
        user = await user_crud.delete(self.db, id=user_id)
        return user is not None

    async def count(self) -> int:
        """Count total users."""
        return await user_crud.count(self.db)

    async def retired_env_logins(self) -> list[str]:
        """Accounts left by the retired env-credential login, which cannot sign in yet."""
        return await user_crud.get_retired_env_logins(self.db)

    async def is_last_admin(self, user_id: int) -> bool:
        """True when this user is the only administrator: demoting or deleting them locks
        everyone out of user management (reset-password restores a login, not the role)."""
        user = await user_crud.get(self.db, user_id)
        if user is None or not user.is_admin:
            return False
        return await user_crud.count_admins(self.db) <= 1

    async def username_exists(
        self, username: str, exclude_id: int | None = None
    ) -> bool:
        """Check if username already exists (optionally excluding a specific user ID)."""
        user = await self.get_by_username(username)
        if user is None:
            return False
        return not (exclude_id is not None and user.id == exclude_id)
