"""Add UniFi Protect username/password to fetch_settings.

Revision ID: 003
Revises: 002
Create Date: 2026-05-16 17:00:00

Adds username and password columns to fetch_settings. Required for private-API
endpoints (recording-snapshot, video/export) which need cookie auth — the
X-API-KEY path only reaches the public Integration API.

Both columns are plaintext, matching the existing api_key column pattern.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "003"
down_revision: str = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    with op.batch_alter_table("fetch_settings") as batch_op:
        if not column_exists("fetch_settings", "username"):
            batch_op.add_column(sa.Column("username", sa.String(length=255), nullable=True))
        if not column_exists("fetch_settings", "password"):
            batch_op.add_column(sa.Column("password", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fetch_settings") as batch_op:
        if column_exists("fetch_settings", "password"):
            batch_op.drop_column("password")
        if column_exists("fetch_settings", "username"):
            batch_op.drop_column("username")
