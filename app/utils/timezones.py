"""The business/display zone — the one place an instant becomes a human day."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app import config
from app.logging_config import get_logger

logger = get_logger(__name__)


def display_zone() -> ZoneInfo:
    """Return the configured display/business zone, falling back to UTC if it is unknown."""
    name = config.DISPLAY_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        logger.warning("Unknown DISPLAY_TIMEZONE %r — falling back to UTC", name)
        return ZoneInfo("UTC")


def to_display(value: datetime) -> datetime:
    """Convert an aware UTC instant into the display zone."""
    return value.astimezone(display_zone())


def business_day(value: datetime | None = None) -> date:
    """Return the calendar day an instant belongs to, in the business zone.

    Use this anywhere a date NAMES something a human sees or the filesystem stores — a
    timelapse's date, a `YYYY/MM` archive folder, a "today's captures" grouping. Computing
    those in UTC would roll every evening capture into the next day and would orphan the
    archive already on disk.
    """
    return to_display(value if value is not None else datetime.now(UTC)).date()


def start_of_business_day(day: date) -> datetime:
    """Return the aware UTC instant at which the given business day begins."""
    return datetime.combine(day, datetime.min.time(), tzinfo=display_zone()).astimezone(
        UTC
    )


def end_of_business_day(day: date) -> datetime:
    """Return the aware UTC instant at which the given business day ends (exclusive)."""
    return datetime.combine(day, datetime.max.time(), tzinfo=display_zone()).astimezone(
        UTC
    )
