"""CRUD operations for User model."""

from datetime import datetime

from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base_crud import CRUDBase
from app.models.user_model import User
from app.schemas.user_schema import UserCreate

# Use argon2 for new passwords, but support legacy bcrypt hashes for existing users
pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated=["bcrypt"])


class CRUDUser(CRUDBase[User, UserCreate, UserCreate]):
    """CRUD operations for User model."""

    async def get_all(self, db: AsyncSession) -> list[User]:
        """Get all users ordered by username."""
        result = await db.execute(select(User).order_by(User.username))
        return list(result.scalars().all())

    async def get_by_username(self, db: AsyncSession, username: str) -> User | None:
        """Get a user by username."""
        result = await db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def create_user(
        self, db: AsyncSession, *, username: str, password: str, is_admin: bool = True
    ) -> User:
        """Create a new user with hashed password."""
        password_hash = pwd_context.hash(password)
        user = User(
            username=username,
            password_hash=password_hash,
            is_admin=is_admin,
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
    ) -> User:
        """Apply field updates to a user (hashing the password if given) and flush."""
        if username is not None:
            user.username = username
        if password is not None:
            user.password_hash = pwd_context.hash(password)
        if is_admin is not None:
            user.is_admin = is_admin
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

    async def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        return pwd_context.verify(plain_password, hashed_password)

    async def authenticate(
        self, db: AsyncSession, username: str, password: str
    ) -> User | None:
        """Authenticate a user by username and password.

        Returns the User if authentication succeeds, None otherwise.
        """
        user = await self.get_by_username(db, username)
        if not user:
            return None
        # Env-sourced users have placeholder hash - can't authenticate via DB
        if user.password_hash == "ENV_MANAGED":  # noqa: S105  (sentinel, not a credential)
            return None
        if not await self.verify_password(password, user.password_hash):
            return None
        return user

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
        user.last_login_at = datetime.now()
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

    async def sync_env_user(self, db: AsyncSession, username: str) -> User:
        """Ensure env user exists in database for tracking purposes.

        Creates the user if it doesn't exist, or updates username if changed.
        Password hash is a placeholder since auth is done against env vars.
        """
        # Find existing env user
        result = await db.execute(
            select(User).where(User.auth_source == User.SOURCE_ENV)
        )
        env_user = result.scalar_one_or_none()

        if env_user:
            # Update username if changed
            if env_user.username != username:
                env_user.username = username
                db.add(env_user)
                await db.flush()
                await db.refresh(env_user)
            return env_user

        # Create new env user record
        env_user = User(
            username=username,
            password_hash="ENV_MANAGED",  # noqa: S106  (sentinel placeholder, not a credential)
            is_admin=True,
            auth_source=User.SOURCE_ENV,
        )
        db.add(env_user)
        await db.flush()
        await db.refresh(env_user)
        return env_user

    async def get_env_user(self, db: AsyncSession) -> User | None:
        """Get the environment-managed user if it exists."""
        result = await db.execute(
            select(User).where(User.auth_source == User.SOURCE_ENV)
        )
        return result.scalar_one_or_none()


user_crud = CRUDUser(User)
