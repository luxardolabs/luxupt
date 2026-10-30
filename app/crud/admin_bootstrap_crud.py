# luxarch:admin-bootstrap-crud asset v3 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit admin-bootstrap-crud`.
"""The user-table half of the admin bootstrap. `luxarch --emit admin-bootstrap-crud`.

Drop this in at `app/crud/admin_bootstrap_crud.py`. It is the ONLY module of the bootstrap that
knows your user model: the core service (`luxarch --emit admin-bootstrap`) calls these functions
and never touches a column. The standard is `luxarch --doc FLEET-ADMIN-BOOTSTRAP-STANDARD`.

What you edit is in the `EDIT THIS` regions: the model import, and the small functions that read
or write its columns. Everything else is the standard and is compared line for line, so
`luxarch --emit admin-bootstrap-crud --update app/crud/admin_bootstrap_crud.py` can carry your
regions onto a newer version.

`count_users` is deliberately NOT editable. "There are no users" is the fact the whole standard
turns on: the seed applies only then, `/setup` exists only then. A count with a filter on it (no
ADMIN users, no user with THIS id) is a different fact. With it, an install whose admins were all
removed would re-create one from an environment password that is still set.

v3: every password set (a recovery reset, a change) ends the user's other sessions through
`_end_sessions` (FLEET-AUTH-SESSION-STANDARD §2.5): an owner resets because someone else got in, and
v2 left that someone signed in. On SQLite, `lock_first_boot` takes the database's write lock: v2's
lock was PostgreSQL-only, so two concurrent `/setup` submissions both saw zero users.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import ColumnElement, delete, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

# isort: split
# --- EDIT THIS: your user model. The fleet home is app/models/user_model.py, class User.
from app.models.user_model import User

# Any constant works. It names the PostgreSQL advisory lock that serialises first-boot creation, so
# two workers (or two replicas) starting on an empty database cannot both create a first admin.
_FIRST_BOOT_LOCK = 7_310_412_908_311_525_401


# --- EDIT THIS: the condition that finds a user by what they sign in with (the core's LOGIN_KIND:
# `User.username == login` for a username login).
def _login_matches(login: str) -> ColumnElement[bool]:
    matches: ColumnElement[bool] = User.username == login
    return matches


# --- EDIT THIS: build the first admin row. Set whatever marks an admin in YOUR model.
def _new_admin(login: str, password_hash: str, *, must_change_password: bool) -> User:
    return User(
        username=login,
        password_hash=password_hash,
        is_admin=True,
        must_change_password=must_change_password,
    )


# --- EDIT THIS: store a new password hash and the forced-change flag on an existing row.
def _store_password(
    user: User, password_hash: str, *, must_change_password: bool
) -> None:
    user.password_hash = password_hash
    user.must_change_password = must_change_password


# --- EDIT THIS: end every session this user holds (FLEET-AUTH-SESSION-STANDARD §2.1 and §2.5). The
# fleet default is a `sessions_valid_after` the auth dependency compares with a token's issued-at; a
# session table deletes the user's rows here instead. It runs in the same transaction as the new hash.
async def _end_sessions(db: AsyncSession, user: User) -> None:
    # A token carries the session_version it was issued under (app/web/auth.py); bumping it refuses
    # every token issued before this password.
    user.session_version += 1


# --- EDIT THIS: the stored hash, for checking the current password on a change.
def password_hash_of(user: User) -> str:
    stored: str = user.password_hash
    return stored


# --- EDIT THIS: whether this user must change their password before doing anything else.
def password_change_required(user: User) -> bool:
    required: bool = user.must_change_password
    return required


async def lock_first_boot(db: AsyncSession) -> None:
    """Hold the first-boot lock until this transaction ends.

    The count-then-insert that follows is only a re-check if nobody else can insert in between.
    Under READ COMMITTED two transactions both see zero users and both insert; the lock makes the
    second one wait, then see the first one's admin, then stop. PostgreSQL: an advisory lock.
    SQLite: a write that matches no row, which takes the database's write lock for the rest of the
    transaction (a second writer waits, up to its busy timeout)."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        await db.execute(select(func.pg_advisory_xact_lock(_FIRST_BOOT_LOCK)))
    elif dialect == "sqlite":
        await db.execute(delete(User).where(false()))


async def count_users(db: AsyncSession) -> int:
    """Every row of the user table, unfiltered. Zero means a first boot."""
    return (await db.scalar(select(func.count()).select_from(User))) or 0


async def get_by_login(db: AsyncSession, login: str) -> User | None:
    return (await db.scalars(select(User).where(_login_matches(login)))).first()


async def add_admin(
    db: AsyncSession, login: str, password_hash: str, *, must_change_password: bool
) -> User:
    user = _new_admin(login, password_hash, must_change_password=must_change_password)
    db.add(user)
    await db.flush()
    return user


async def set_password(
    db: AsyncSession, user: User, password_hash: str, *, must_change_password: bool
) -> None:
    _store_password(user, password_hash, must_change_password=must_change_password)
    await _end_sessions(db, user)
    await db.flush()
