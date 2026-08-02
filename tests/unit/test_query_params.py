"""Unit tests for the query-param normalization helpers (web/query_params.py).

Locks the empty-safe behavior the `fw.enum_query_empty_safe` guard enforces: a
filter dropdown's "All" option posts '' and must map to None BEFORE enum
validation — a bare optional enum param would 422 on it (see LUXUPT enum sweep).
"""

import pytest
from app.models.enum_model import ActivityType, TimelapseStatus
from pydantic import TypeAdapter, ValidationError
from app.web.query_params import (
    ActivityTypeFilter,
    IntFilter,
    ThumbnailSizeFilter,
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


class TestIntFilters:
    """Non-str optional query params (fw.optional_query_empty_safe): '' → None, not a 422."""

    def test_int_filter_empty_is_none(self) -> None:
        # The bug the rule guards: a bare `int | None` 422s on the dropdown's blank "".
        assert TypeAdapter(IntFilter).validate_python("") is None

    def test_int_filter_valid_value(self) -> None:
        assert TypeAdapter(IntFilter).validate_python("60") == 60

    def test_thumbnail_size_empty_is_none(self) -> None:
        assert TypeAdapter(ThumbnailSizeFilter).validate_python("") is None

    def test_thumbnail_size_bounds_preserved(self) -> None:
        assert TypeAdapter(ThumbnailSizeFilter).validate_python("256") == 256
        with pytest.raises(ValidationError):
            TypeAdapter(ThumbnailSizeFilter).validate_python("2000")  # > le=1024
