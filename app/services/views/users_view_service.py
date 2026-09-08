"""Users view service for preparing user management template data."""

from typing import Any

from app.logging_config import get_logger
from app.services.core.user_core_service import UserCoreService

logger = get_logger(__name__)


class UsersViewService:
    """Prepares data and handles logic for user management pages."""

    def __init__(self, user_service: UserCoreService):
        """Initialize with core user service."""
        self.user_service = user_service

    async def get_users_context(self) -> dict[str, Any]:
        """Get context for users list page.

        All users (both env and database) are stored in the users table.
        The auth_source field indicates where credentials come from.
        """
        # No sync here: the env user is synced ONCE at startup (web/main lifespan). Doing it
        # on render made this GET write to the database (fw.state_changing_get).
        users = await self.user_service.get_all()
        return {"users": users}

    async def get_user_form_context(self, user_id: int | None = None) -> dict[str, Any]:
        """Get context for user create/edit form."""
        edit_user = None
        if user_id:
            edit_user = await self.user_service.get_by_id(user_id)
        return {
            "edit_user": edit_user,
            # Route knowledge lives in the view (fw.url_assembly_in_view); create and edit
            # post to different endpoints.
            "user_form_post_url": (
                f"/system/users/{edit_user.id}" if edit_user else "/system/users"
            ),
        }

    async def get_delete_confirm_context(self, user_id: int) -> dict[str, Any]:
        """Get context for delete confirmation panel."""
        delete_user = await self.user_service.get_by_id(user_id)
        user_count = await self.user_service.count()
        return {
            "delete_user": delete_user,
            "user_count": user_count,
            # The view assembles URLs (fw.no_template_logic). None when the user is gone —
            # the template already branches on delete_user, and mypy caught that this was
            # not guarded: a deleted/unknown id would have raised here.
            "user_delete_url": f"/system/users/{delete_user.id}"
            if delete_user
            else None,
        }

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
            logger.debug(
                "Validation errors", extra={"errors": errors, "username": username}
            )

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
        if password and password != confirm_password:
            errors.append("Passwords do not match")

        return errors

    async def create_user(
        self,
        username: str,
        password: str,
        is_admin: bool,
    ) -> tuple[bool, str, str | None]:
        """Create a new user. Returns (success, message, created username)."""
        username = username.strip()
        try:
            user = await self.user_service.create(
                username=username,
                password=password,
                is_admin=is_admin,
            )
            return True, f"User '{username}' created successfully", user.username
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
