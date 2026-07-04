"""
Jinja2 template filters for LuxUPT.

All filters handle None gracefully, returning "--" when the input is None.

DATE FORMATS ({{ dt | date }} or {{ dt | date('format') }}):
    default     "2024-01-15"            ISO format
    short       "1/15/24"               Compact US style
    medium      "Jan 15, 2024"          Month abbrev
    long        "January 15, 2024"      Full month name
    full        "Monday, January 15, 2024"  With weekday

TIME FORMATS ({{ dt | time }} or {{ dt | time('format') }}):
    default     "2:30 pm"               12h no seconds
    short       "2:30 pm"               12h no seconds
    medium      "2:30:45 pm"            12h with seconds
    24h         "14:30"                 24h no seconds
    12h         "2:30 pm"               12h no seconds

DATETIME FORMATS ({{ dt | format_datetime }} or {{ dt | format_datetime('format') }}):
    default     "Jan 15, 2024, 2:30 pm" Medium format
    short       "1/15/24, 2:30 pm"      Compact US style
    medium      "Jan 15, 2024, 2:30 pm" Month abbrev
    long        "January 15, 2024 at 2:30:45 pm"  Full format
    friendly    "Jan 15 at 2:30 pm"     Casual format
    compact     "2024-01-15, 2:30 pm"   For tables/lists
    log         "2024-01-15, 2:30:45 pm" Log format with seconds

RELATIVE TIME ({{ dt | timeago }}):
    "5m ago", "2h ago", "3d ago"

DURATION ({{ seconds | duration }}):
    "2h 15m", "3d 5h"
"""

from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates


# Named format presets
DATE_FORMATS = {
    "default": "%Y-%m-%d",  # 2024-01-15
    "short": "%-m/%-d/%y",  # 1/15/24
    "medium": "%b %-d, %Y",  # Jan 15, 2024
    "long": "%B %-d, %Y",  # January 15, 2024
    "full": "%A, %B %-d, %Y",  # Monday, January 15, 2024
}

TIME_FORMATS = {
    "default": "%-I:%M %P",  # 2:30 pm
    "short": "%-I:%M %P",  # 2:30 pm
    "medium": "%-I:%M:%S %P",  # 2:30:45 pm
    "24h": "%H:%M",  # 14:30
    "12h": "%-I:%M %P",  # 2:30 pm
}

DATETIME_FORMATS = {
    "default": "%b %-d, %Y, %-I:%M %P",  # Jan 15, 2024, 2:30 pm
    "short": "%-m/%-d/%y, %-I:%M %P",  # 1/15/24, 2:30 pm
    "medium": "%b %-d, %Y, %-I:%M %P",  # Jan 15, 2024, 2:30 pm
    "long": "%B %-d, %Y at %-I:%M:%S %P",  # January 15, 2024 at 2:30:45 pm
    "friendly": "%b %-d at %-I:%M %P",  # Jan 15 at 2:30 pm
    "compact": "%Y-%m-%d, %-I:%M %P",  # 2024-01-15, 2:30 pm (for tables/lists)
    "log": "%Y-%m-%d, %-I:%M:%S %P",  # 2024-01-15, 2:30:45 pm
}


def _get_format(fmt: str, format_dict: dict[str, str]) -> str:
    """Get format string from named format or use as-is if custom."""
    if fmt in format_dict:
        return format_dict[fmt]
    return fmt


def format_datetime(dt: datetime | None, fmt: str = "default") -> str:
    """Format a datetime as a full date/time string.

    Args:
        dt: A datetime object
        fmt: Named format or strftime format string

    Returns:
        Formatted string or '--' if dt is None.

    Usage:
        {{ dt | format_datetime }}           -> "Jan 15, 2024, 2:30 pm"
        {{ dt | format_datetime('friendly') }} -> "Jan 15 at 2:30 pm"
    """
    if dt is None:
        return "--"
    if fmt == "relative":
        return timeago(dt)
    format_str = _get_format(fmt, DATETIME_FORMATS)
    return dt.strftime(format_str)


def format_date(dt: datetime | None, fmt: str = "default") -> str:
    """Format a datetime as a date string.

    Args:
        dt: A datetime object
        fmt: Named format or strftime format string

    Returns:
        Formatted string or '--' if dt is None.

    Usage:
        {{ dt | date }}              -> "2024-01-15"
        {{ dt | date('medium') }}    -> "Jan 15, 2024"
    """
    if dt is None:
        return "--"
    format_str = _get_format(fmt, DATE_FORMATS)
    return dt.strftime(format_str)


def format_time(dt: datetime | time | None, fmt: str = "default") -> str:
    """Format a datetime or time as a time string.

    Args:
        dt: A datetime or time object
        fmt: Named format or strftime format string

    Returns:
        Formatted string or '--' if dt is None.

    Usage:
        {{ dt | time }}          -> "2:30 pm"
        {{ dt | time('24h') }}   -> "14:30"
    """
    if dt is None:
        return "--"
    format_str = _get_format(fmt, TIME_FORMATS)
    return dt.strftime(format_str)


# Abbreviations for time units
_TIME_UNITS = [
    (86400, "d"),
    (3600, "h"),
    (60, "m"),
    (1, "s"),
]


def timeago(dt: datetime | None, now: datetime | None = None) -> str:
    """Format a datetime as a relative time string.

    Args:
        dt: A datetime object
        now: Current time for comparison (defaults to now)

    Returns:
        Relative time string like "5m ago" or "2h ago"

    Usage:
        {{ capture.capture_datetime | timeago }}  -> "5m ago"
    """
    if dt is None:
        return "--"

    if now is None:
        now = datetime.now()

    # Handle future times
    if dt > now:
        return "just now"

    delta_seconds = int((now - dt).total_seconds())

    if delta_seconds < 60:
        return "just now"

    for unit_seconds, unit_abbrev in _TIME_UNITS:
        if delta_seconds >= unit_seconds:
            value = delta_seconds // unit_seconds
            return f"{value}{unit_abbrev} ago"

    return "just now"


def duration(value: int | float | timedelta | None, style: str = "short") -> str:
    """Format a duration in seconds or timedelta as a human-readable string.

    Args:
        value: Number of seconds or a timedelta object
        style: 'short' for '1h 30m', 'long' for '1 hour 30 minutes'

    Returns:
        Formatted duration string

    Usage:
        {{ uptime_seconds | duration }}  -> "2h 15m"
        {{ timedelta_value | duration }}  -> "1h 30m"
    """
    if value is None:
        return "--"

    if isinstance(value, timedelta):
        seconds = int(value.total_seconds())
    else:
        seconds = int(value)
    if seconds < 0:
        return "--"

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    if style == "long":
        parts = []
        if days > 0:
            parts.append(f"{days} {'day' if days == 1 else 'days'}")
        if hours > 0:
            parts.append(f"{hours} {'hour' if hours == 1 else 'hours'}")
        if minutes > 0:
            parts.append(f"{minutes} {'minute' if minutes == 1 else 'minutes'}")
        if secs > 0 and not parts:
            parts.append(f"{secs} {'second' if secs == 1 else 'seconds'}")
        return " ".join(parts) if parts else "0 seconds"

    # short style
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 and not parts:
        parts.append(f"{secs}s")
    return " ".join(parts) if parts else "0s"


def date_range(start_dt: datetime | None, end_dt: datetime | None, period: str = "24h") -> str:
    """Format a date range for display.

    Args:
        start_dt: Start datetime
        end_dt: End datetime
        period: Period type ('1h', '6h', '24h', '7d', '30d')

    Returns:
        Formatted range string like "Jan 15 2:30 pm - 8:30 pm" or "Jan 15 - Jan 22, 2024"
    """
    if start_dt is None or end_dt is None:
        return "--"

    if period in ("1h", "6h"):
        if start_dt.date() == end_dt.date():
            return f"{start_dt.strftime('%b %-d %-I:%M %P')} - {end_dt.strftime('%-I:%M %P')}"
        else:
            return f"{start_dt.strftime('%b %-d %-I:%M %P')} - {end_dt.strftime('%b %-d %-I:%M %P')}"
    elif period == "24h":
        if start_dt.date() == end_dt.date():
            return start_dt.strftime("%b %-d, %Y")
        else:
            return f"{start_dt.strftime('%b %-d')} - {end_dt.strftime('%b %-d, %Y')}"
    else:
        return f"{start_dt.strftime('%b %-d')} - {end_dt.strftime('%b %-d, %Y')}"


def number_format(value: int | float | None) -> str:
    """Format a number with thousands separator commas.

    Args:
        value: A number (int or float)

    Returns:
        Formatted string with commas (e.g., "1,234,567")

    Usage:
        {{ stats.total | number_format }}  -> "12,345"
    """
    if value is None:
        return "0"
    return f"{int(value):,}"


def file_size_filter(bytes_value: int | float | None) -> str:
    """Format bytes as human-readable file size.

    Args:
        bytes_value: Size in bytes

    Returns:
        Formatted string like "1.5 MB" or "256 KB"

    Usage:
        {{ capture.file_size | file_size }}  -> "1.2 MB"
    """
    if bytes_value is None:
        return "--"

    bytes_value = float(bytes_value)
    if bytes_value >= 1073741824:
        return f"{bytes_value / 1073741824:.2f} GB"
    elif bytes_value >= 1048576:
        return f"{bytes_value / 1048576:.1f} MB"
    elif bytes_value >= 1024:
        return f"{bytes_value / 1024:.1f} KB"
    else:
        return f"{int(bytes_value)} B"


def register_filters(templates: "Jinja2Templates") -> None:
    """Register all custom filters with a Jinja2Templates instance.

    Args:
        templates: The Jinja2Templates instance to add filters to.

    Usage in main.py:
        from web.template_filters import register_filters
        templates = Jinja2Templates(directory="templates")
        register_filters(templates)
    """
    # Date/time filters
    templates.env.filters["format_datetime"] = format_datetime
    templates.env.filters["date"] = format_date
    templates.env.filters["time"] = format_time
    templates.env.filters["timeago"] = timeago
    templates.env.filters["duration"] = duration
    # Number/formatting filters
    templates.env.filters["number_format"] = number_format
    templates.env.filters["file_size"] = file_size_filter

    # Global functions and data
    templates.env.globals["date_range"] = date_range
    templates.env.globals["current_year"] = datetime.now(UTC).year
