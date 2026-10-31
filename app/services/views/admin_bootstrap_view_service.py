# luxarch:admin-bootstrap-view asset v5 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit admin-bootstrap-view`.
"""The web seam of the admin bootstrap. `luxarch --emit admin-bootstrap-view`.

Drop this in at `app/services/views/admin_bootstrap_view_service.py`. It turns the `/setup` form into
a call on the core service (`luxarch --emit admin-bootstrap`) and the core's refusals into what the
page shows. It also answers the two questions a login handler asks: where does the login page go
while there are no users, and where does a user who just signed in go.

Every path it hands back is resolved from a route NAME through the request (`request.url_for(name)`,
its path only), so a page that moves, a mount prefix and the app's `root_path` all reach every
redirect and every comparison with the path the request arrived on (`fw.urls_from_route_names`,
`luxarch --playbook url-in-view`).

A form re-rendered with an error carries back only text the page can be written in: a login holding
a character UTF-8 cannot encode (a lone surrogate, which a client reaches by naming the form's
charset) is shown empty, and an error message shows such a character as its `\\uXXXX` escape.

The standard is `luxarch --doc FLEET-ADMIN-BOOTSTRAP-STANDARD`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

# isort: split
# --- EDIT THIS: the core service (fleet home shown).
from app.services.core.admin_bootstrap_core_service import (
    LOGIN_KIND,
    AdminBootstrapCoreService,
    PasswordPolicyError,
    SetupClosedError,
    SetupTokenError,
)

# The name both `/setup` routes register (`luxarch --emit admin-bootstrap-router`).
SETUP_ROUTE = "admin.setup"

# --- EDIT THIS: the route NAMES of your login page and password-change form.
LOGIN_ROUTE = "pages.login_page"
CHANGE_PASSWORD_ROUTE = "account.change_password_page"


# --- EDIT THIS: the route name of logout, which a user who must change their password may still
# reach.
LOGOUT_ROUTE = "pages.logout_page"


@dataclass(frozen=True)
class SetupForm:
    token: str
    login: str  # what the user signs in with, an email or a username per LOGIN_KIND
    password: str
    password_confirm: str


@dataclass(frozen=True)
class SetupOutcome:
    """What the router renders: a redirect, the form again with an error, or 404 (closed)."""

    closed: bool = False
    redirect: str = ""
    status_code: int = 200
    context: dict[str, str] = field(default_factory=dict)


class AdminBootstrapViewService:
    def __init__(self) -> None:
        self.core = AdminBootstrapCoreService()

    async def setup_page(self, db: AsyncSession) -> dict[str, str] | None:
        """The empty form's context, or None when `/setup` no longer exists."""
        if not await self.core.setup_open(db):
            return None
        return {"login": "", "login_kind": LOGIN_KIND, "error": ""}

    async def submit_setup(
        self, request: Request, db: AsyncSession, form: SetupForm
    ) -> SetupOutcome:
        login = form.login.strip()
        if form.password != form.password_confirm:
            return self._again(login, "The passwords do not match.", 400)
        try:
            await self.core.create_first_admin(db, form.token, login, form.password)
        except SetupClosedError:
            return SetupOutcome(closed=True)
        except SetupTokenError:
            return self._again(
                login, "That is not the setup token printed in the server log.", 403
            )
        except PasswordPolicyError as exc:
            return self._again(login, f"Not accepted: {exc}.", 400)
        return SetupOutcome(redirect=_path(request, LOGIN_ROUTE))

    async def login_page_redirect(self, request: Request, db: AsyncSession) -> str:
        """Where the login page sends a visitor instead of rendering: the setup form while there are
        no users to sign in as, otherwise "" (render the login page)."""
        return _path(request, SETUP_ROUTE) if await self.core.setup_open(db) else ""

    async def login_destination(
        self, request: Request, db: AsyncSession, login: str, next_path: str
    ) -> str:
        """Where a user goes straight after signing in. A seeded or reset account goes to the change
        form first."""
        if await self.core.needs_password_change(db, login):
            return _path(request, CHANGE_PASSWORD_ROUTE)
        return next_path

    async def forced_change_redirect(
        self, request: Request, db: AsyncSession, login: str, path: str
    ) -> str:
        """Where an authenticated request for `path` goes instead, "" to let it through. The app's
        authentication (the dependency or middleware that loads the signed-in user) calls this on
        EVERY request: a seeded or reset account reaches only the change form and logout until it
        changes the password. A redirect at login alone is a suggestion: the session cookie it set
        opens every other page and the API."""
        if not await self.core.needs_password_change(db, login):
            return ""
        change = _path(request, CHANGE_PASSWORD_ROUTE)
        if path in (change, _path(request, LOGOUT_ROUTE)):
            return ""
        return change

    @staticmethod
    def _again(login: str, error: str, status_code: int) -> SetupOutcome:
        shown = login if _escaped(login) == login else ""
        return SetupOutcome(
            status_code=status_code,
            context={
                "login": shown,
                "login_kind": LOGIN_KIND,
                "error": _escaped(error),
            },
        )


def _escaped(text: str) -> str:
    """`text` as a UTF-8 page carries it: each lone surrogate becomes its `\\uXXXX` escape."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _path(request: Request, name: str) -> str:
    """The route's path as the browser addresses it: the outermost router resolves the name (so a
    mount prefix is included) and the request's base adds the app's `root_path`."""
    return request.url_for(name).path
