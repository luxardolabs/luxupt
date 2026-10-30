# luxarch:admin-bootstrap asset v3 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit admin-bootstrap`.
"""How the first admin comes to exist, and how a locked-out owner gets back in.

`luxarch --emit admin-bootstrap`. Drop this in at `app/services/core/admin_bootstrap_core_service.py`.
The standard it implements is `luxarch --doc FLEET-ADMIN-BOOTSTRAP-STANDARD`; read it first.

## The set (emit all of them; each carries its own marker)

| emit                         | lands at                                                   | layer  |
| ---------------------------- | ---------------------------------------------------------- | ------ |
| `admin-bootstrap`            | `app/services/core/admin_bootstrap_core_service.py` (this) | core   |
| `admin-bootstrap-crud`       | `app/crud/admin_bootstrap_crud.py`                         | crud   |
| `admin-bootstrap-view`       | `app/services/views/admin_bootstrap_view_service.py`       | view   |
| `admin-bootstrap-router`     | `app/web/routers/admin_bootstrap_router.py`                | router |
| `admin-bootstrap-cli`        | `app/cli.py`                                               | owner  |
| `admin-bootstrap-form`       | `app/templates/web/partials/admin_setup_form.html`         | markup |

Wiring, four lines in code you own:

- the app's lifespan awaits `bootstrap_on_startup()` from `app.cli` (it owns its own transaction:
  list `app/cli.py` in `[allowlist].non_request_files`);
- the app includes `admin_bootstrap_router.router`;
- the login handler sends a signed-in user to `AdminBootstrapViewService().login_destination(...)`,
  and the login page to `login_page_redirect(...)` while there are no users;
- the app's authentication redirects every request to `forced_change_redirect(...)` when it returns a
  path (v3), so a seeded or reset account reaches only the change form and logout.

## What runs, in order

1. **Startup.** Inside one transaction, under a lock: if the user table has ANY row, nothing is
   seeded, the setup token is discarded, and the log says so. Otherwise the seed runs if
   `INITIAL_ADMIN_EMAIL` (or `INITIAL_ADMIN_USERNAME`, per LOGIN_KIND) and `INITIAL_ADMIN_PASSWORD` (or `INITIAL_ADMIN_PASSWORD_FILE`, for Docker
   secrets) are set: the admin is created with `must_change_password` set. A missing, partial or
   rejected seed never stops startup. It logs why and opens `/setup` instead, with a one-time token
   printed in the log.
2. **`/setup`.** Exists only while there are zero users (404 afterwards). It re-checks that under
   the same lock, inside the transaction that creates the admin, and compares the submitted token
   in constant time. The route is rate-limited.
3. **Recovery.** `docker compose exec app python -m app.cli reset-password <login>` prints a random
   temporary password once and sets `must_change_password`. Shell access is the proof of ownership;
   there is no email dependency.
4. **Sign-in.** `login_destination` sends a user whose `must_change_password` is set to the change
   form before anything else, and the app's authentication calls `forced_change_redirect` on every
   request so that user reaches nothing else until they change it.

v3, from adopters' adversarial passes: the app's own password policy is consulted
(`password_policy_problem`), a change to the current password is refused, a password the hasher
refuses (bcrypt past 72 bytes) is a policy error on `/setup` and a change, not a 500, a login longer
than 254 characters is refused before it reaches a column, every password set ends the user's
sessions (the crud's `_end_sessions`; a user's own change signs them in again with a fresh cookie),
and `/setup` no longer deletes the token inside its transaction: a commit that failed left `/setup`
open with no token that worked. The token is discarded once the transaction that created the admin
commits, and wherever users are seen to exist (`setup_open`).
The forced change is enforced on every request by the view's `forced_change_redirect`, called from
the app's authentication, not only at the login redirect.

What is deliberately absent: an environment password applied on every boot. Once a user exists the
database is authoritative. Re-applying the environment silently reverts the owner's own change.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

# isort: split
# --- EDIT THIS: the crud module and your password hashing (fleet homes shown).
from app.crud import admin_bootstrap_crud
from app.utils.password_hash import hash_password as get_password_hash
from app.utils.password_hash import verify_password

logger = logging.getLogger(__name__)

# --- EDIT THIS: the shortest password the seed, /setup and a change accept.
MIN_PASSWORD_LENGTH = 12


# --- EDIT THIS: the app's own password policy, after the length check: a message for a password it
# refuses, "" for one it accepts. Point it at the rules every later change of the account must meet,
# so the first admin (the most privileged account) cannot be created with a password they refuse.
# dropped-input: this app's whole policy is MIN_PASSWORD_LENGTH, checked before this runs and by
# every user form (users_view_service), so there is nothing further to judge the password by
async def password_policy_problem(db: AsyncSession, password: str) -> str:
    return ""


# --- EDIT THIS: where the one-time setup token lives while there are no users. Every worker in one
# container reads the same file, so whichever worker serves POST /setup knows the token in the log.
SETUP_TOKEN_FILE = Path(tempfile.gettempdir()) / "admin-setup-token"

# --- EDIT THIS: what a user signs in with, "email" or "username". It names the seed's login variable
# (INITIAL_ADMIN_EMAIL or INITIAL_ADMIN_USERNAME), the check a login must pass, and the setup form's
# field. v2: v1 hard-wired email, so an app whose users sign in by username could not adopt it unedited.
LOGIN_KIND = "username"

# The seed's inputs. The names are the standard's: one spelling fleet-wide.
SEED_LOGIN = f"INITIAL_ADMIN_{LOGIN_KIND.upper()}"
SEED_PASSWORD = "INITIAL_ADMIN_PASSWORD"
_LOGIN_SHAPE = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+$"),
    "username": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$"),
}
# The longest address SMTP carries (RFC 5321): a longer login would overflow the user table's column
# inside the insert, and the failing statement's parameters (the hash among them) reach the log.
_MAX_LOGIN = 254


class BootstrapOutcome(StrEnum):
    USERS_EXIST = "users_exist"
    SEEDED = "seeded"
    SETUP = "setup"
    SEED_REJECTED = "seed_rejected"


@dataclass(frozen=True)
class BootstrapResult:
    """Which path startup took. `setup_token` is set only when `/setup` is open."""

    outcome: BootstrapOutcome
    detail: str
    setup_token: str | None = None


class SetupClosedError(Exception):
    """There is already a user: `/setup` does not exist."""


class SetupTokenError(Exception):
    """The submitted token is not the one in the server log."""


class PasswordPolicyError(ValueError):
    """A login or password the policy refuses. The message is safe to show."""


class UnknownUserError(LookupError):
    """No user signs in with that login."""


class CurrentPasswordError(ValueError):
    """The current password given for a change is wrong."""


def _env_value(name: str) -> tuple[str, str]:
    """(value, problem) for `name` or `<name>_FILE`. Both set is refused rather than guessed."""
    direct = os.environ.get(name, "").strip()
    path = os.environ.get(f"{name}_FILE", "").strip()
    if direct and path:
        return "", f"{name} and {name}_FILE are both set; set one"
    if not path:
        return direct, ""
    try:
        return Path(path).read_text(encoding="utf-8").strip(), ""
    except OSError as exc:
        return "", f"{name}_FILE ({path}) cannot be read: {exc.strerror}"


def _login_problem(login: str) -> str:
    shape = _LOGIN_SHAPE.get(LOGIN_KIND)
    if shape is None:
        return f"LOGIN_KIND is {LOGIN_KIND!r}, not 'email' or 'username'"
    if len(login) > _MAX_LOGIN:
        return f"the login is longer than {_MAX_LOGIN} characters"
    if shape.match(login):
        return ""
    return (
        "the login is not an email address"
        if LOGIN_KIND == "email"
        else "the login is not a username (letters, digits, '.', '_' or '-', up to 64)"
    )


async def _password_problem(db: AsyncSession, password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"the password is shorter than {MIN_PASSWORD_LENGTH} characters"
    return await password_policy_problem(db, password)


def _hash(password: str) -> str:
    """The hash, or PasswordPolicyError when the hasher refuses the password (length, encoding)."""
    try:
        hashed: str = get_password_hash(password)
    except ValueError as exc:
        raise PasswordPolicyError(
            f"the password was refused by the hasher: {exc}"
        ) from None
    return hashed


def _issue_token(path: Path) -> str:
    """The current setup token, creating it if no worker has yet.

    Written to a private temporary file and then hard-linked into place, so a second worker either
    links first or reads a complete token: never an empty file mid-write."""
    token = secrets.token_urlsafe(32)
    staged = path.with_name(f"{path.name}.{secrets.token_hex(8)}")
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    try:
        os.link(staged, path)
    except FileExistsError:
        token = path.read_text(encoding="utf-8").strip()
    finally:
        staged.unlink(missing_ok=True)
    return token


def _read_token(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _discard_token(path: Path) -> None:
    path.unlink(missing_ok=True)


def _discard_setup_token(_session: object) -> None:
    _discard_token(SETUP_TOKEN_FILE)


class AdminBootstrapCoreService:
    """First boot, `/setup`, recovery and the forced change. Transport-agnostic: the view, the CLI
    and the startup hook all call these methods; none of them opens a transaction here."""

    async def bootstrap(self, db: AsyncSession) -> BootstrapResult:
        """Startup's one call. Seeds the first admin from the environment only while the user table
        is empty, and otherwise says why not. Never raises for a bad seed."""
        await admin_bootstrap_crud.lock_first_boot(db)
        if await admin_bootstrap_crud.count_users(db) > 0:
            await asyncio.to_thread(_discard_token, SETUP_TOKEN_FILE)
            return self._logged(
                BootstrapResult(
                    BootstrapOutcome.USERS_EXIST,
                    "users exist: the INITIAL_ADMIN_* seed is ignored and /setup is closed",
                )
            )
        login, login_problem = _env_value(SEED_LOGIN)
        password, password_problem = _env_value(SEED_PASSWORD)
        problem = login_problem or password_problem
        if not (login or password or problem):
            return await self._open_setup(
                BootstrapOutcome.SETUP, "no INITIAL_ADMIN_* seed is configured"
            )
        if not problem and not (login and password):
            problem = f"set both {SEED_LOGIN} and {SEED_PASSWORD}"
        problem = (
            problem or _login_problem(login) or await _password_problem(db, password)
        )
        password_hash = ""
        if not problem:
            try:
                password_hash = _hash(password)
            except PasswordPolicyError as exc:
                problem = str(exc)
        if problem:
            return await self._open_setup(BootstrapOutcome.SEED_REJECTED, problem)
        await admin_bootstrap_crud.add_admin(
            db, login, password_hash, must_change_password=True
        )
        return self._logged(
            BootstrapResult(
                BootstrapOutcome.SEEDED,
                f"seeded the first admin {login} from INITIAL_ADMIN_*; "
                "they must change the password at first sign-in",
            )
        )

    async def setup_open(self, db: AsyncSession) -> bool:
        """`/setup` exists only while there are zero users. Once users exist the one-time token is
        discarded here, so a token from an earlier empty state cannot claim the install again if its
        users are later removed (`/setup` itself leaves it until its transaction has landed)."""
        users: int = await admin_bootstrap_crud.count_users(db)
        if users:
            await asyncio.to_thread(_discard_token, SETUP_TOKEN_FILE)
        return users == 0

    async def create_first_admin(
        self, db: AsyncSession, token: str, login: str, password: str
    ) -> None:
        """Create the first admin through `/setup`. Re-checks under the first-boot lock, in the
        caller's transaction, that there is still no user, then checks the one-time token."""
        await admin_bootstrap_crud.lock_first_boot(db)
        if await admin_bootstrap_crud.count_users(db) > 0:
            await asyncio.to_thread(_discard_token, SETUP_TOKEN_FILE)
            raise SetupClosedError
        expected = await asyncio.to_thread(_read_token, SETUP_TOKEN_FILE)
        if not expected or not hmac.compare_digest(
            token.strip().encode(), expected.encode()
        ):
            logger.warning("admin_setup_token_refused", extra={"login": login})
            raise SetupTokenError
        problem = _login_problem(login) or await _password_problem(db, password)
        if problem:
            raise PasswordPolicyError(problem)
        # The admin chose this password, so no forced change follows.
        await admin_bootstrap_crud.add_admin(
            db, login, _hash(password), must_change_password=False
        )
        # The token goes only once the admin row has landed: a failed transaction leaves it working
        # (v2 deleted it first, so a retry got 403 until a restart), a landed one leaves no token from
        # this empty state to claim the install with should its users later be removed.
        event.listen(db.sync_session, "after_commit", _discard_setup_token, once=True)
        logger.info("admin_setup_completed", extra={"login": login})

    async def reset_password(self, db: AsyncSession, login: str) -> str:
        """Give `login` a random temporary password, returned once, that must be changed at the next
        sign-in. The recovery CLI calls this; so may an admin's reset-another-user route."""
        user = await admin_bootstrap_crud.get_by_login(db, login)
        if user is None:
            raise UnknownUserError(login)
        temporary = secrets.token_urlsafe(15)
        await admin_bootstrap_crud.set_password(
            db, user, _hash(temporary), must_change_password=True
        )
        logger.warning("password_reset_to_temporary", extra={"login": login})
        return temporary

    async def change_password(
        self, db: AsyncSession, login: str, current_password: str, new_password: str
    ) -> None:
        """A user's own change: the current password is required, the new one must differ from it,
        and the forced change clears. Every session ends (the caller signs the user in again)."""
        user = await admin_bootstrap_crud.get_by_login(db, login)
        if user is None:
            raise UnknownUserError(login)
        stored = admin_bootstrap_crud.password_hash_of(user)
        if not verify_password(current_password, stored):
            raise CurrentPasswordError
        problem = await _password_problem(db, new_password)
        if not problem and verify_password(new_password, stored):
            problem = "the new password is the current one"
        if problem:
            raise PasswordPolicyError(problem)
        await admin_bootstrap_crud.set_password(
            db, user, _hash(new_password), must_change_password=False
        )

    async def needs_password_change(self, db: AsyncSession, login: str) -> bool:
        """True while `login` is still on a seeded or reset password."""
        user = await admin_bootstrap_crud.get_by_login(db, login)
        return user is not None and admin_bootstrap_crud.password_change_required(user)

    async def _open_setup(
        self, outcome: BootstrapOutcome, detail: str
    ) -> BootstrapResult:
        token = await asyncio.to_thread(_issue_token, SETUP_TOKEN_FILE)
        return self._logged(BootstrapResult(outcome, detail, token))

    @staticmethod
    def _logged(result: BootstrapResult) -> BootstrapResult:
        """Startup logs which path ran. The token is logged ON PURPOSE: reading the server log is
        the proof of ownership that claims an exposed, empty instance."""
        if result.setup_token is None:
            logger.info(
                "admin_bootstrap",
                extra={"outcome": result.outcome.value, "detail": result.detail},
            )
        else:
            logger.warning(
                "admin_bootstrap",
                extra={
                    "outcome": result.outcome.value,
                    "detail": result.detail,
                    "setup": f"open /setup and enter the token {result.setup_token}",
                },
            )
        return result
