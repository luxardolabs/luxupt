"""Unit tests for the query-param normalization helpers (web/query_params.py).

Locks the empty-safe behavior the `fw.enum_query_empty_safe` guard enforces: a
filter dropdown's "All" option posts '' and must map to None BEFORE enum
validation — a bare optional enum param would 422 on it (see LUXUPT enum sweep).
"""

from models.enum_model import ActivityType, TimelapseStatus
from pydantic import TypeAdapter
from web.query_params import (
    ActivityTypeFilter,
    TimelapseStatusFilter,
    empty_to_none,
)


class TestEmptyToNone:
    def test_blank_becomes_none(self) -> None:
        assert empty_to_none("") is None

    def test_real_value_passes_through(self) -> None:
        assert empty_to_none("completed") == "completed"

    def test_non_str_passes_through(self) -> None:
        assert empty_to_none(None) is None
        assert empty_to_none(5) == 5


class TestEnumFilters:
    """The regression the empty-safe rule prevents: '' → None, not a 422."""

    def test_timelapse_status_empty_is_none(self) -> None:
        assert TypeAdapter(TimelapseStatusFilter).validate_python("") is None

    def test_timelapse_status_valid_value(self) -> None:
        assert (
            TypeAdapter(TimelapseStatusFilter).validate_python("completed")
            is TimelapseStatus.COMPLETED
        )

    def test_activity_type_empty_is_none(self) -> None:
        assert TypeAdapter(ActivityTypeFilter).validate_python("") is None

    def test_activity_type_valid_value(self) -> None:
        assert (
            TypeAdapter(ActivityTypeFilter).validate_python("capture_failed")
            is ActivityType.CAPTURE_FAILED
        )
