"""User service for user management operations."""

from typing import cast

import config
from crud.user_crud import user_crud
from models.user import User
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession


class UserService:
    """Service for user management operations."""

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    async def get_all(self) -> list[User]:
        """Get all users ordered by username."""
        return await user_crud.get_all(self.db)

    async def get_by_id(self, user_id: int) -> User | None:
        """Get a user by ID."""
        return await user_crud.get(self.db, user_id)

    async def get_by_username(self, username: str) -> User | None:
        """Get a user by username."""
        return await user_crud.get_by_username(self.db, username)

    async def create(self, username: str, password: str, is_admin: bool = True) -> User:
        """Create a new user."""
        return await user_crud.create_user(
            self.db,
            username=username,
            password=password,
            is_admin=is_admin,
        )

    async def update(
        self,
        user_id: int,
        *,
        username: str | None = None,
        password: str | None = None,
        is_admin: bool | None = None,
    ) -> User | None:
        """Update an existing user."""
        user = await self.get_by_id(user_id)
        if not user:
            return None

        if username is not None:
            user.username = username
        if password is not None:
            pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
            user.password_hash = cast(str, pwd_context.hash(password))
        if is_admin is not None:
            user.is_admin = is_admin

        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def delete(self, user_id: int) -> bool:
        """Delete a user by ID. Returns True if deleted, False if not found."""
        user = await user_crud.delete(self.db, id=user_id)
        return user is not None

    async def count(self) -> int:
        """Count total users."""
        return await user_crud.count(self.db)

    async def username_exists(self, username: str, exclude_id: int | None = None) -> bool:
        """Check if username already exists (optionally excluding a specific user ID)."""
        user = await self.get_by_username(username)
        if user is None:
            return False
        if exclude_id is not None and user.id == exclude_id:
            return False
        return True

    def has_env_auth(self) -> bool:
        """Check if environment authentication is configured."""
        return bool(config.WEB_PASSWORD)

    async def sync_env_user(self) -> User | None:
        """Sync environment user to database for tracking.

        If WEB_PASSWORD is set, ensures a corresponding user record exists
        in the database with auth_source='env'. Returns the user or None
        if env auth is not configured.
        """
        if not self.has_env_auth():
            return None

        return await user_crud.sync_env_user(self.db, config.WEB_USERNAME)

    async def get_env_user(self) -> User | None:
        """Get the environment-managed user from database."""
        return await user_crud.get_env_user(self.db)
