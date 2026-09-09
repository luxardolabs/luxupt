"""Column types that keep datetimes tz-aware UTC across the driver boundary."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """A ``DateTime`` that is tz-aware UTC on BOTH sides of the driver.

    ``DateTime(timezone=True)`` alone is not enough on SQLite. SQLite has no native
    ``timestamptz``: SQLAlchemy's SQLite ``DATETIME`` storage format carries no offset field,
    so an aware value is written WITHOUT its offset and read back NAIVE. Measured on this
    stack — write ``datetime.now(UTC)``, read back ``tzinfo=None`` — which makes every
    ``row.created_at < datetime.now(UTC)`` a ``TypeError: can't compare offset-naive and
    offset-aware datetimes``. The column type being ``timezone=True`` is not the safety net
    it looks like; it is the Postgres answer applied to a driver that ignores it.

    This closes the gap at the only place both directions pass through:

    * **bind** — normalise to UTC, and REJECT a naive value. Rejecting is deliberate: a naive
      value here is a construction site that was missed, and silently storing a local
      wall-clock as though it were UTC is exactly the corruption this type exists to stop.
      A loud failure names the site; a silent one shifts an instant by the local offset.
    * **result** — re-attach UTC to the offset-less value the driver hands back.

    The stored text stays the same shape as before (``YYYY-MM-DD HH:MM:SS.ffffff``), so
    ordering, ``BETWEEN`` and index use are unchanged — what changes is that the wall-clock
    in that text is UTC, and that reads come back aware.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        """Normalise an aware value to UTC; refuse a naive one."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime bound to a UtcDateTime column — construct it as "
                "datetime.now(UTC) (see luxarch --playbook datetime-utc)"
            )
        return value.astimezone(UTC)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        """Re-attach UTC to the offset-less value the driver returns."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        """Keep the underlying column type identical to the plain aware DateTime."""
        return dialect.type_descriptor(DateTime(timezone=True))
