"""Drop stale override_deletion column from jobs table.

Revision ID: 002
Revises: 001
Create Date: 2026-02-20 01:00:00

Migration 001 added keep_images as a semantic replacement for override_deletion,
but never removed the old column. The leftover override_deletion column is NOT NULL
with no default, causing every INSERT to fail with:
    sqlite3.IntegrityError: NOT NULL constraint failed: jobs.override_deletion

This migration drops the stale column so job creation works again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: str = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Drop override_deletion column from jobs table if it exists."""
    if column_exists("jobs", "override_deletion"):
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.drop_column("override_deletion")


def downgrade() -> None:
    """Re-add override_deletion column to jobs table."""
    if not column_exists("jobs", "override_deletion"):
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.add_column(
                sa.Column("override_deletion", sa.Boolean(), nullable=False, server_default=sa.text("0"))
            )
