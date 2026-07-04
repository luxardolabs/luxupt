"""Add keep_images column to jobs table.

Revision ID: 001
Revises: None
Create Date: 2026-02-04 01:00:00

This is the initial migration for LuxUPT. It adds the keep_images column
which was renamed from override_deletion for semantic clarity.

For existing databases: adds the missing column.
For new databases: column already exists from create_all, migration is skipped.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add keep_images column to jobs table if it doesn't exist."""
    # Check if column already exists (e.g., from create_all on new databases)
    if not column_exists("jobs", "keep_images"):
        # Use batch mode for SQLite compatibility
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.add_column(sa.Column("keep_images", sa.Boolean(), nullable=False, server_default=sa.text("1")))


def downgrade() -> None:
    """Remove keep_images column from jobs table."""
    if column_exists("jobs", "keep_images"):
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.drop_column("keep_images")
