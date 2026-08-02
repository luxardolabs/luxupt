"""Add end_date column to timelapses for multi-day combined timelapses.

Revision ID: 005
Revises: 004
Create Date: 2026-05-17 09:00:00

Combined historical timelapses span a range of days, but the schema only had
a single `timelapse_date`. Without an end date, the browser had no way to
display the range and these timelapses appeared as if they belonged to the
start date alone.

`end_date` is nullable — null for single-day timelapses (live_daily and
historical), populated for combined-range outputs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "005"
down_revision: str = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not column_exists("timelapses", "end_date"):
        with op.batch_alter_table("timelapses") as batch_op:
            batch_op.add_column(sa.Column("end_date", sa.Date(), nullable=True))


def downgrade() -> None:
    if column_exists("timelapses", "end_date"):
        with op.batch_alter_table("timelapses") as batch_op:
            batch_op.drop_column("end_date")
