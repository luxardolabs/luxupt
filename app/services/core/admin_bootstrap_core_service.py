# luxarch:admin-bootstrap asset v4 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit admin-bootstrap`.
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

Text UTF-8 cannot encode (a lone surrogate) is refused as the wrong value of its field, never raised
as an encoding error. A client reaches one by naming a form charset (`charset=unicode_escape`), the
environment and the command line through bytes that are not UTF-8. A setup token holding one is a
wrong token (403, `admin_setup_token_refused`), a login or password holding one is a policy error,
a current password holding one is wrong, and a seed file that is not UTF-8 is a rejected seed. A
login the database's driver cannot bind (a lone surrogate; NUL on PostgreSQL, which SQLite
stores) signs in as nobody, and the forced-change check fails closed on it. A new login (the seed, `/setup`) holding any control character
(NUL, which PostgreSQL refuses in text, a newline) is a policy error, and a password the hasher refuses to CHECK
(bcrypt past 72 bytes) is a wrong current password or a refused new one, as one it refuses to hash
is. A seed file written with a byte-order mark seeds the login after it.

The setup token file is trusted only as this process's own private file: a regular file, not a
link, a pipe or a directory, owned by its user, unreadable by others. While one that is not (another
local user planted it in a shared temp directory before first boot) holds the path, `/setup`
refuses every token and the log says `admin_setup_token_unavailable`, with the path and what to
do: remove a file that is not this process's own, or move `SETUP_TOKEN_FILE` off a filesystem
without hard links.

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
import stat
import tempfile
import unicodedata
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


def _encodable(text: str) -> bool:
    """Whether UTF-8 can carry `text`: a lone surrogate is the one `str` it cannot. No column, hasher
    or stored token holds such text, so it is the wrong value of whatever field it was sent as."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _bindable(db: AsyncSession, text: str) -> bool:
    """Whether this database's driver can bind `text` as a query parameter: UTF-8 can encode it,
    and on PostgreSQL it holds no NUL (PostgreSQL refuses NUL in text; SQLite stores it). No row's
    login is anything else, so a login that cannot be bound is nobody's."""
    if not _encodable(text):
        return False
    return "\x00" not in text or db.get_bind().dialect.name != "postgresql"


def _env_value(name: str) -> tuple[str, str]:
    """(value, problem) for `name` or `<name>_FILE`. Both set is refused rather than guessed."""
    direct = os.environ.get(name, "").strip()
    path = os.environ.get(f"{name}_FILE", "").strip()
    if direct and path:
        return "", f"{name} and {name}_FILE are both set; set one"
    if not path:
        return direct, ""
    try:
        # utf-8-sig: an editor's byte-order mark is not part of the login or password
        return Path(path).read_text(encoding="utf-8-sig").strip(), ""
    except OSError as exc:
        return "", f"{name}_FILE ({path}) cannot be read: {exc.strerror}"
    except UnicodeDecodeError:
        return "", f"{name}_FILE ({path}) is not UTF-8 text"


def _login_problem(login: str) -> str:
    shape = _LOGIN_SHAPE.get(LOGIN_KIND)
    if shape is None:
        return f"LOGIN_KIND is {LOGIN_KIND!r}, not 'email' or 'username'"
    if len(login) > _MAX_LOGIN:
        return f"the login is longer than {_MAX_LOGIN} characters"
    if not _encodable(login):
        return "the login holds a character UTF-8 cannot encode"
    if any(unicodedata.category(c) == "Cc" for c in login):
        return "the login holds a control character"
    if shape.match(login):
        return ""
    return (
        "the login is not an email address"
        if LOGIN_KIND == "email"
        else "the login is not a username (letters, digits, '.', '_' or '-', up to 64)"
    )


async def _password_problem(db: AsyncSession, password: str) -> str:
    if not _encodable(password):
        return "the password holds a character UTF-8 cannot encode"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"the password is shorter than {MIN_PASSWORD_LENGTH} characters"
    return await password_policy_problem(db, password)


def _hash(password: str) -> str:
    """The hash, or PasswordPolicyError when the hasher refuses the password (length, encoding)."""
    try:
        hashed: str = get_password_hash(password)
    except ValueError as exc:
        # Logged too: a hasher that cannot load refuses every password, and that must not read only
        # as the user's input.
        refused = type(exc).__qualname__
        logger.warning("password_hash_refused", extra={"error": refused})
        raise PasswordPolicyError(
            f"the password was refused by the hasher: {exc}"
        ) from None
    return hashed


def _verified(password: str, stored: str) -> bool:
    """Whether `password` is the one `stored` hashes. One the hasher refuses to check (bcrypt past
    72 bytes raises ValueError) is not: every stored hash came from a password it accepted. The
    refusal is logged, because a hasher that cannot load (a passlib backend that does not match
    its bcrypt) refuses every password, and that must not read as users mistyping."""
    try:
        ok: bool = verify_password(password, stored)
    except ValueError as exc:
        refused = type(exc).__qualname__
        logger.warning("password_check_refused", extra={"error": refused})
        return False
    return ok


def _issue_token(path: Path) -> tuple[str, str]:
    """(the current setup token, ""), creating it if no worker has yet, or ("", why there is none).

    Written to a private temporary file and then hard-linked into place, so a second worker either
    links first or reads a complete token: never an empty file mid-write."""
    staged = path.with_name(f"{path.name}.{secrets.token_hex(8)}")
    try:
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        return "", (
            f"the token file cannot be created in {path.parent} ({exc.strerror}): set "
            "SETUP_TOKEN_FILE to a file in a writable directory"
        )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_urlsafe(32))
        os.link(staged, path)
    except FileExistsError:
        # Another worker's token, or a file this process did not write (one another local user
        # planted in a shared temp directory would be a token they know): never replaced, so no
        # two workers can each log a token of their own.
        pass
    except OSError as exc:
        return "", (
            f"the token file cannot be written into place ({exc.strerror}): set "
            "SETUP_TOKEN_FILE to a file on a writable filesystem with hard links"
        )
    finally:
        staged.unlink(missing_ok=True)
    # Read back through the same check `/setup` applies, so the log never shows a token it refuses.
    return _token_at(path)


_NOT_OWN = (
    "the token file is not this process's own private file (a regular file it owns that no "
    "other user can read): remove it and restart"
)


def _token_at(path: Path) -> tuple[str, str]:
    """(the token in `path`, "") or ("", why it holds none). Trusted only as a regular file (not a
    link, a pipe or a directory) this process's user owns that no other user can read or write.
    Opened without blocking, so a pipe planted at the path cannot hang the caller."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        why = f"the token file cannot be opened ({exc.strerror})"
        return "", f"{why}: remove it and restart"
    try:
        st = os.fstat(fd)
        # POSIX: a platform without owners checks the type only
        euid = getattr(os, "geteuid", None)
        if not (
            stat.S_ISREG(st.st_mode)
            and (euid is None or (st.st_uid == euid() and not st.st_mode & 0o077))
        ):
            return "", _NOT_OWN
        data = os.read(fd, 4096)
    except OSError as exc:
        why = f"the token file cannot be read ({exc.strerror})"
        return "", f"{why}: remove it and restart"
    finally:
        os.close(fd)
    try:
        token = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        token = ""
    if not token:
        return "", "the token file holds no token: remove it and restart"
    return token, ""


def _read_token(path: Path) -> str:
    """The token `/setup` accepts: the one in `path` when it is trusted (`_token_at`), else ""."""
    return _token_at(path)[0]


def _discard_token(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # a file another user owns in a sticky temp directory
        logger.warning(
            "admin_setup_token_not_discarded",
            extra={"path": str(path), "error": exc.strerror},
        )


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
        submitted = token.strip()
        if (
            not expected
            or not _encodable(submitted)
            or not hmac.compare_digest(submitted.encode(), expected.encode())
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
        sign-in. The recovery CLI calls this; so may an admin's reset-another-user route, with a
        login someone typed: one the driver cannot bind (a lone surrogate, NUL on PostgreSQL) is
        nobody, never a query that fails inside the driver."""
        user = (
            await admin_bootstrap_crud.get_by_login(db, login)
            if _bindable(db, login)
            else None
        )
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
        and the forced change clears. Every session ends (the caller signs the user in again).
        A login the driver cannot bind (a lone surrogate, NUL on PostgreSQL), which no row holds,
        is nobody; every other login, NUL included where the database stores it, is looked up."""
        user = (
            await admin_bootstrap_crud.get_by_login(db, login)
            if _bindable(db, login)
            else None
        )
        if user is None:
            raise UnknownUserError(login)
        stored = admin_bootstrap_crud.password_hash_of(user)
        if not _encodable(current_password) or not _verified(current_password, stored):
            raise CurrentPasswordError
        problem = await _password_problem(db, new_password)
        if not problem and _verified(new_password, stored):
            problem = "the new password is the current one"
        if problem:
            raise PasswordPolicyError(problem)
        await admin_bootstrap_crud.set_password(
            db, user, _hash(new_password), must_change_password=False
        )

    async def needs_password_change(self, db: AsyncSession, login: str) -> bool:
        """True while `login` is still on a seeded or reset password. A login the driver cannot
        bind (a lone surrogate, NUL on PostgreSQL) cannot be looked up, so the answer fails
        closed: the change form, not every page."""
        if not _bindable(db, login):
            return True
        user = await admin_bootstrap_crud.get_by_login(db, login)
        return user is not None and admin_bootstrap_crud.password_change_required(user)

    async def _open_setup(
        self, outcome: BootstrapOutcome, detail: str
    ) -> BootstrapResult:
        token, problem = await asyncio.to_thread(_issue_token, SETUP_TOKEN_FILE)
        if not token:
            logger.warning(
                "admin_setup_token_unavailable",
                extra={
                    "path": str(SETUP_TOKEN_FILE),
                    "detail": f"every /setup token is refused: {problem}",
                },
            )
            return self._logged(BootstrapResult(outcome, detail))
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
