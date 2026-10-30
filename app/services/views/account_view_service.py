"""The signed-in user's own account: changing their password (FLEET-ADMIN-BOOTSTRAP-STANDARD §5)."""

from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.account_schema import PasswordChangeOutcome
from app.services.core.admin_bootstrap_core_service import (
    MIN_PASSWORD_LENGTH,
    AdminBootstrapCoreService,
    CurrentPasswordError,
    PasswordPolicyError,
    UnknownUserError,
)


class AccountViewService:
    """Shapes the change-password form and turns the core's refusals into what the page shows."""

    def __init__(self) -> None:
        self.core = AdminBootstrapCoreService()

    async def change_password_context(
        self, db: AsyncSession, username: str
    ) -> dict[str, Any]:
        """The empty form. `forced` says the user is here because their password is temporary."""
        return {
            "error": None,
            "forced": await self.core.needs_password_change(db, username),
            "min_password_length": MIN_PASSWORD_LENGTH,
        }

    async def submit_change(
        self,
        request: Request,
        db: AsyncSession,
        username: str,
        current_password: str,
        new_password: str,
        confirm_password: str,
    ) -> PasswordChangeOutcome:
        """Change the user's own password; the current one is required."""
        error = ""
        status_code = 400
        if new_password != confirm_password:
            error = "The new passwords do not match."
        elif new_password == current_password:
            error = "Choose a password different from the current one."
        else:
            try:
                await self.core.change_password(
                    db, username, current_password, new_password
                )
            except CurrentPasswordError, UnknownUserError:
                error, status_code = "The current password is not correct.", 403
            except PasswordPolicyError as exc:
                error = f"Not accepted: {exc}."
            else:
                # The change ended every session, this one included: sign in with the new one.
                login = request.app.url_path_for("pages.login_page")
                return PasswordChangeOutcome(
                    redirect=f"{login}?{urlencode({'password_changed': '1'})}"
                )
        context = await self.change_password_context(db, username)
        context["error"] = error
        return PasswordChangeOutcome(status_code=status_code, context=context)
