"""Add source column to scheduler_settings (captured vs historical).

Revision ID: 006
Revises: 005
Create Date: 2026-08-14 01:00:00

The nightly scheduler can build each day's timelapse from live captures (the
existing behavior) or by fetching the day's frames from Protect's recordings.
`source` records that choice; existing installs default to 'captured' so
behavior is unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "006"
down_revision: str = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not column_exists("scheduler_settings", "source"):
        with op.batch_alter_table("scheduler_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "source",
                    sa.String(length=16),
                    nullable=False,
                    server_default="captured",
                )
            )


def downgrade() -> None:
    if column_exists("scheduler_settings", "source"):
        with op.batch_alter_table("scheduler_settings") as batch_op:
            batch_op.drop_column("source")
