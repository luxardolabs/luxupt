"""randomize users.session_version on existing rows

A session token names its user by username and carries the row's ``session_version``, so a token
fits any row holding that name at that version. Every version the app writes is random. This gives
existing rows, which sit at small numbers close to each other, the same protection. Every session
issued before this revision ends, so each user signs in once more.

The downgrade changes nothing: any integer is a valid version for the previous release.

Revision ID: 7c1e4f9a2b3d
Revises: 5b8e2d71c4a9
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c1e4f9a2b3d"
down_revision: str | None = "5b8e2d71c4a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    user_ids: Sequence[int] = (
        bind.execute(sa.text("SELECT id FROM users")).scalars().all()
    )
    for user_id in user_ids:
        bind.execute(
            sa.text("UPDATE users SET session_version = :version WHERE id = :id"),
            {"version": secrets.randbits(62), "id": user_id},
        )


def downgrade() -> None:
    pass
