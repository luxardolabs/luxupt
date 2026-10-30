"""CRUD operations for User model."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base_crud import CRUDBase
from app.models.user_model import User
from app.schemas.user_schema import UserCreate
from app.utils.password_hash import hash_password, verify_password

# Argon2 for new passwords, legacy bcrypt still verifiable — see app/utils/password_hash.py
# for why this no longer goes through passlib's argon2 handler.


class CRUDUser(CRUDBase[User, UserCreate, UserCreate]):
    """CRUD operations for User model."""

    async def get_all(self, db: AsyncSession) -> list[User]:
        """Get all users ordered by username."""
        # unbounded-reads: the sign-in accounts of one install, created by its admin: a handful
        result = await db.execute(select(User).order_by(User.username))
        return list(result.scalars().all())

    async def get_by_username(self, db: AsyncSession, username: str) -> User | None:
        """Get a user by username."""
        result = await db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def create_user(
        self,
        db: AsyncSession,
        *,
        username: str,
        password: str,
        is_admin: bool = True,
        must_change_password: bool = False,
    ) -> User:
        """Create a new user with hashed password."""
        password_hash = hash_password(password)
        user = User(
            username=username,
            password_hash=password_hash,
            is_admin=is_admin,
            must_change_password=must_change_password,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

    async def update_user(
        self,
        db: AsyncSession,
        user: User,
        *,
        username: str | None = None,
        password: str | None = None,
        is_admin: bool | None = None,
        must_change_password: bool | None = None,
    ) -> User:
        """Apply field updates to a user (hashing the password if given) and flush."""
        if username is not None:
            user.username = username
        if password is not None:
            user.password_hash = hash_password(password)
            user.session_version += (
                1  # a new password ends the sessions issued before it
            )
        if must_change_password is not None:
            user.must_change_password = must_change_password
        if is_admin is not None:
            user.is_admin = is_admin
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

    async def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        return verify_password(plain_password, hashed_password)

    async def authenticate(
        self, db: AsyncSession, username: str, password: str
    ) -> User | None:
        """Authenticate a user by username and password.

        Returns the User if authentication succeeds, None otherwise.
        """
        user = await self.get_by_username(db, username)
        if not user:
            return None
        if not await self.verify_password(password, user.password_hash):
            return None
        return user

    async def get_retired_env_logins(self, db: AsyncSession) -> list[str]:
        """Usernames still on the retired WEB_PASSWORD login's placeholder hash, which verifies
        against nothing: they sign in again only after reset-password."""
        result = await db.execute(
            select(User.username).where(User.password_hash == "ENV_MANAGED")
        )
        return list(result.scalars().all())

    async def count_admins(self, db: AsyncSession) -> int:
        """How many users are administrators."""
        result = await db.execute(
            select(func.count(User.id)).where(User.is_admin.is_(True))
        )
        return result.scalar() or 0

    async def user_exists(self, db: AsyncSession) -> bool:
        """Check if any users exist in the database."""
        result = await db.execute(select(func.count(User.id)))
        count = result.scalar() or 0
        return count > 0

    async def update_last_login(self, db: AsyncSession, user_id: int) -> User | None:
        """Update the last_login_at timestamp for a user."""
        user = await self.get(db, user_id)
        if not user:
            return None
        user.last_login_at = datetime.now(UTC)
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user


user_crud = CRUDUser(User)
