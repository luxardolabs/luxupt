"""Users view service for preparing user management template data."""

from logging_config import get_logger
from models.user import User

from services.user_service import UserService

logger = get_logger(__name__)


class UsersViewService:
    """Prepares data and handles logic for user management pages."""

    def __init__(self, user_service: UserService):
        """Initialize with core user service."""
        self.user_service = user_service

    async def get_users_context(self) -> dict:
        """Get context for users list page.

        All users (both env and database) are stored in the users table.
        The auth_source field indicates where credentials come from.
        """
        # Sync env user to ensure it exists in database if configured
        await self.user_service.sync_env_user()

        users = await self.user_service.get_all()
        return {"users": users}

    async def get_user_form_context(self, user_id: int | None = None) -> dict:
        """Get context for user create/edit form."""
        edit_user = None
        if user_id:
            edit_user = await self.user_service.get_by_id(user_id)
        return {"edit_user": edit_user}

    async def get_delete_confirm_context(self, user_id: int) -> dict:
        """Get context for delete confirmation panel."""
        delete_user = await self.user_service.get_by_id(user_id)
        user_count = await self.user_service.count()
        return {"delete_user": delete_user, "user_count": user_count}

    async def validate_user_create(
        self,
        username: str,
        password: str,
        confirm_password: str,
    ) -> list[str]:
        """Validate user creation input. Returns list of error messages."""
        errors = []

        # Username validation
        username = username.strip()
        if not username:
            errors.append("Username is required")
        elif len(username) > 64:
            errors.append("Username must be 64 characters or less")
        elif await self.user_service.username_exists(username):
            errors.append("Username already exists")

        # Password validation
        if not password:
            errors.append("Password is required")
        elif password != confirm_password:
            errors.append("Passwords do not match")

        if errors:
            logger.debug("Validation errors", extra={"errors": errors, "username": username})

        return errors

    async def validate_user_update(
        self,
        user_id: int,
        username: str,
        password: str,
        confirm_password: str,
    ) -> list[str]:
        """Validate user update input. Returns list of error messages."""
        errors = []

        # Username validation
        username = username.strip()
        if not username:
            errors.append("Username is required")
        elif len(username) > 64:
            errors.append("Username must be 64 characters or less")
        elif await self.user_service.username_exists(username, exclude_id=user_id):
            errors.append("Username already exists")

        # Password validation (only if provided)
        if password:
            if password != confirm_password:
                errors.append("Passwords do not match")

        return errors

    async def create_user(
        self,
        username: str,
        password: str,
        is_admin: bool,
    ) -> tuple[bool, str, User | None]:
        """Create a new user. Returns (success, message, user)."""
        username = username.strip()
        try:
            user = await self.user_service.create(
                username=username,
                password=password,
                is_admin=is_admin,
            )
            return True, f"User '{username}' created successfully", user
        except Exception as e:
            logger.exception("Failed to create user", extra={"username": username})
            return False, f"Failed to create user: {e}", None

    async def update_user(
        self,
        user_id: int,
        username: str,
        password: str | None,
        is_admin: bool,
    ) -> tuple[bool, str]:
        """Update an existing user. Returns (success, message)."""
        username = username.strip()
        try:
            user = await self.user_service.update(
                user_id,
                username=username,
                password=password if password else None,
                is_admin=is_admin,
            )
            if not user:
                return False, "User not found"
            return True, f"User '{username}' updated successfully"
        except Exception as e:
            logger.exception("Failed to update user", extra={"user_id": user_id})
            return False, f"Failed to update user: {e}"

    async def delete_user(self, user_id: int) -> tuple[bool, str]:
        """Delete a user. Returns (success, message)."""
        # Prevent deleting the last user
        user_count = await self.user_service.count()
        if user_count <= 1:
            return False, "Cannot delete the last user"

        # Get user info before deletion for message
        user = await self.user_service.get_by_id(user_id)
        username = user.username if user else "unknown"

        try:
            deleted = await self.user_service.delete(user_id)
            if not deleted:
                return False, "User not found"
            return True, f"User '{username}' deleted successfully"
        except Exception as e:
            logger.exception("Failed to delete user", extra={"user_id": user_id})
            return False, f"Failed to delete user: {e}"
