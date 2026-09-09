"""Unit tests for the pure Jinja template filters (web/template_filters.py).

These format user-facing values on every page; a regression shows up as wrong or
crashing output in the templates the render smokes exercise. All are pure — no DB,
no wall-clock (timeago takes an explicit `now`).
"""

from datetime import UTC, datetime, timedelta

from app.web.template_filters import (
    duration,
    file_size_filter,
    number_format,
    timeago,
)

NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


class TestDuration:
    def test_none_and_negative(self) -> None:
        assert duration(None) == "--"
        assert duration(-5) == "--"

    def test_short_style(self) -> None:
        assert duration(0) == "0s"
        assert duration(45) == "45s"
        assert (
            duration(90) == "1m"
        )  # sub-minute seconds dropped once a larger unit shows
        assert duration(3600) == "1h"
        assert duration(3661) == "1h 1m"
        assert duration(90061) == "1d 1h 1m"

    def test_long_style(self) -> None:
        assert duration(90, style="long") == "1 minute"
        assert duration(3661, style="long") == "1 hour 1 minute"

    def test_accepts_timedelta(self) -> None:
        assert duration(timedelta(minutes=2)) == "2m"


class TestNumberFormat:
    def test_thousands_separator(self) -> None:
        assert number_format(1234567) == "1,234,567"
        assert number_format(12345) == "12,345"
        assert number_format(0) == "0"

    def test_none_is_zero(self) -> None:
        assert number_format(None) == "0"


class TestFileSize:
    def test_units(self) -> None:
        assert file_size_filter(0) == "0 B"
        assert file_size_filter(512) == "512 B"
        assert file_size_filter(1024) == "1.0 KB"
        assert file_size_filter(1536) == "1.5 KB"
        assert file_size_filter(1048576) == "1.0 MB"
        assert file_size_filter(1073741824) == "1.00 GB"

    def test_none(self) -> None:
        assert file_size_filter(None) == "--"


class TestTimeago:
    def test_none(self) -> None:
        assert timeago(None, NOW) == "--"

    def test_future_and_sub_minute_are_just_now(self) -> None:
        assert timeago(NOW + timedelta(seconds=60), NOW) == "just now"
        assert timeago(NOW - timedelta(seconds=30), NOW) == "just now"

    def test_relative_units(self) -> None:
        assert timeago(NOW - timedelta(minutes=5), NOW) == "5m ago"
        assert timeago(NOW - timedelta(hours=2), NOW) == "2h ago"
        assert timeago(NOW - timedelta(days=1), NOW) == "1d ago"
