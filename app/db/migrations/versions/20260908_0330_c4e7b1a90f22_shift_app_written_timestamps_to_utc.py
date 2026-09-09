"""shift app-written timestamps from naive local to UTC

Until this revision the app constructed every timestamp with ``datetime.now()`` — naive, and
naive means *local* to whatever machine wrote it. Storage was already declared aware
(``DateTime(timezone=True)``), but that declaration does nothing on SQLite: SQLAlchemy's SQLite
DATETIME storage format has no offset field, so the offset is dropped on write and reads come
back naive. Measured on this stack before writing this migration.

``UtcDateTime`` (``app/db/types.py``) now re-attaches UTC on read. That is correct for every
value written from here on, and WRONG for every row already in the table: a value that means
"14:00 Chicago" would start reading as "14:00 UTC" — the same wall-clock, a different instant,
displayed five or six hours late. This migration converts the existing rows so the new read
path tells the truth about them.

Scope — only columns the APPLICATION wrote:

  activities.timestamp · cameras.{last_seen_at,last_capture_at,first_discovered_at}
  captures.capture_datetime · jobs.{start_at,end_at,created_at,started_at,completed_at}
  scheduler_settings.last_run_at · timelapses.{started_at,completed_at} · users.last_login_at

``created_at``/``updated_at`` on the ``TimestampMixin`` tables are deliberately EXCLUDED: they
carry ``server_default=func.now()``, and SQLite's ``CURRENT_TIMESTAMP`` is **already UTC**, so
they were never local and shifting them would break them. ``jobs.created_at`` is NOT one of
those — ``jobs`` does not use the mixin and wrote its own value — so it IS shifted. Getting
that distinction wrong in either direction silently moves data, which is why it is spelled out
rather than inferred from the column name.

``captures.capture_date`` is untouched: it is a ``Date`` naming a local calendar day, which is
still exactly what it means (see ``app/utils/timezones.business_day``).

The conversion runs per-row in Python rather than as a SQL ``'+N hours'`` offset, because the
offset is not constant: a fixed shift would be an hour wrong on every row written on the other
side of a DST boundary. ``ZoneInfo`` resolves each instant against the rules in force at that
instant. Ambiguous local times (the repeated hour when clocks go back) resolve to the first
occurrence, which is what a clock in that hour would have read first.

If the deployment's zone IS UTC, every branch is a no-op and the migration exits early.

Revision ID: c4e7b1a90f22
Revises: 9f3d0d41053d
"""

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

revision: str = "c4e7b1a90f22"
down_revision: str | None = "9f3d0d41053d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) pairs the application wrote as naive local time.
APP_WRITTEN: tuple[tuple[str, str], ...] = (
    ("activities", "timestamp"),
    ("cameras", "last_seen_at"),
    ("cameras", "last_capture_at"),
    ("cameras", "first_discovered_at"),
    ("captures", "capture_datetime"),
    ("jobs", "start_at"),
    ("jobs", "end_at"),
    ("jobs", "created_at"),
    ("jobs", "started_at"),
    ("jobs", "completed_at"),
    ("scheduler_settings", "last_run_at"),
    ("timelapses", "started_at"),
    ("timelapses", "completed_at"),
    ("users", "last_login_at"),
)

_STORAGE = "%Y-%m-%d %H:%M:%S.%f"


def _zone() -> ZoneInfo:
    """Return the zone the existing naive values were written in."""
    name = os.getenv("DISPLAY_TIMEZONE") or os.getenv("TZ") or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        raise RuntimeError(
            f"cannot convert stored timestamps: unknown timezone {name!r}. Set "
            "DISPLAY_TIMEZONE (or TZ) to the zone this deployment's data was written in."
        ) from None


def _parse(raw: str) -> datetime | None:
    """Parse a stored SQLite datetime string, tolerating a missing microsecond part."""
    for fmt in (_STORAGE, "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)  # noqa: DTZ007 - stored value is naive by definition
        except ValueError:
            continue
    return None


def _shift(to_utc: bool) -> None:
    """Rewrite every app-written timestamp between local and UTC."""
    zone = _zone()
    if zone == ZoneInfo("UTC"):
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table, column in APP_WRITTEN:
        if table not in tables:
            continue
        rows = bind.execute(
            sa.text(f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL")
        ).fetchall()
        updates = []
        for rowid, raw in rows:
            naive = _parse(str(raw))
            if naive is None or naive.tzinfo is not None:
                continue
            if to_utc:
                shifted = naive.replace(tzinfo=zone).astimezone(UTC)
            else:
                shifted = naive.replace(tzinfo=UTC).astimezone(zone)
            updates.append({"v": shifted.strftime(_STORAGE), "r": rowid})
        if updates:
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :v WHERE rowid = :r"),
                updates,
            )


def upgrade() -> None:
    """Reinterpret stored naive-local timestamps as the UTC instants they represent."""
    _shift(to_utc=True)


def downgrade() -> None:
    """Put the timestamps back into local wall-clock, for a rollback to the old read path."""
    _shift(to_utc=False)
