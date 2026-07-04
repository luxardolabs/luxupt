"""Extend jobs table for historical timelapse jobs.

Revision ID: 004
Revises: 003
Create Date: 2026-05-16 18:00:00

Adds the columns historical jobs need to express a date range and an
optional daily window:

  job_type            'live_daily' (existing behavior, default) | 'historical'
  start_at            timestamp range start (historical only)
  end_at              timestamp range end (historical only)
  daily_window_start  optional time-of-day filter start (e.g., 07:00:00)
  daily_window_end    optional time-of-day filter end (e.g., 19:00:00)

All new columns are nullable. Existing rows get job_type='live_daily' via the
column default; no backfill required.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "004"
down_revision: str = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        if not column_exists("jobs", "job_type"):
            batch_op.add_column(
                sa.Column("job_type", sa.String(length=32), nullable=False, server_default=sa.text("'live_daily'"))
            )
        if not column_exists("jobs", "start_at"):
            batch_op.add_column(sa.Column("start_at", sa.DateTime(timezone=True), nullable=True))
        if not column_exists("jobs", "end_at"):
            batch_op.add_column(sa.Column("end_at", sa.DateTime(timezone=True), nullable=True))
        if not column_exists("jobs", "daily_window_start"):
            batch_op.add_column(sa.Column("daily_window_start", sa.Time(), nullable=True))
        if not column_exists("jobs", "daily_window_end"):
            batch_op.add_column(sa.Column("daily_window_end", sa.Time(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        for col in ("daily_window_end", "daily_window_start", "end_at", "start_at", "job_type"):
            if column_exists("jobs", col):
                batch_op.drop_column(col)
