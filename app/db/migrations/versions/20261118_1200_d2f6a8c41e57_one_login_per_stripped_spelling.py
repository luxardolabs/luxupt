"""store every login stripped, and allow one login per stripped spelling

A login is what a user types to sign in, without the whitespace around it: ` bob`, `bob\\t` and
`bob` are one login. This stores each existing login stripped and adds a unique index on the
stripped form, so two rows can never share one login.

The upgrade refuses, changing nothing, when two stored logins are the same once stripped, or when a
login is nothing but whitespace: which row keeps the login is the operator's decision. It names the
rows and the database file this run opened; the operator renames one and starts the stack again.
The message carries no command to paste, since a log format may escape its quoting. A login it
strips keeps its sessions: a token is resolved by its stripped login.

The downgrade drops the index and leaves the stripped logins as they are, which the previous
release reads the same way.

Revision ID: d2f6a8c41e57
Revises: a4d9e6b01c7f
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2f6a8c41e57"
down_revision: str | None = "a4d9e6b01c7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_users_login_identity"
# Exactly the characters Python's str.strip() removes, written out: a migration does not import
# the app, whose constant may change after this revision has run.
WHITESPACE = "".join(
    chr(c)
    for c in (
        0x09,
        0x0A,
        0x0B,
        0x0C,
        0x0D,
        0x1C,
        0x1D,
        0x1E,
        0x1F,
        0x20,
        0x85,
        0xA0,
        0x1680,
        0x2000,
        0x2001,
        0x2002,
        0x2003,
        0x2004,
        0x2005,
        0x2006,
        0x2007,
        0x2008,
        0x2009,
        0x200A,
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    )
)


class LoginCollisionError(RuntimeError):
    """Stored logins this revision cannot store stripped without merging two accounts."""


def upgrade() -> None:
    bind = op.get_bind()
    rows: Sequence[tuple[int, str]] = [
        (int(row[0]), str(row[1]))
        for row in bind.execute(sa.text("SELECT id, username FROM users")).all()
    ]
    by_login: dict[str, list[tuple[int, str]]] = {}
    for user_id, username in rows:
        by_login.setdefault(username.strip(), []).append((user_id, username))

    problems = [
        f"users {', '.join(f'id {i} ({n!r})' for i, n in same)} all sign in as {login!r}"
        for login, same in by_login.items()
        if len(same) > 1 and login
    ]
    problems += [
        f"user id {i} has a login of whitespace only ({n!r})"
        for i, n in by_login.get("", [])
    ]
    if problems:
        raise LoginCollisionError(
            "cannot store every login stripped: "
            + "; ".join(problems)
            + f". Rename one of each in {os.path.abspath(bind.engine.url.database or '')}, "
            "then start the stack again (the release's upgrade notes give the steps)."
        )

    for user_id, username in rows:
        if username != username.strip():
            bind.execute(
                sa.text("UPDATE users SET username = :username WHERE id = :id"),
                {"username": username.strip(), "id": user_id},
            )
    op.create_index(
        INDEX,
        "users",
        [sa.func.trim(sa.column("username"), sa.literal(WHITESPACE))],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="users")
