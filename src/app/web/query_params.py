"""Typed query-parameter filters — the one standard for enum-ish filters.

A closed-set filter (status / type / …) is typed as its **enum**, so FastAPI
validates it at the boundary (a bad value is a 422, the allowed values land in
OpenAPI) and nothing downstream ever sees a loose string. "No filter" is the
absence of the param or an empty string (the dropdown's "All" option), which a
single ``BeforeValidator`` normalizes to ``None`` **before** enum validation.

Mirrors the fleet-canonical ``app/core/query_params.py`` (luxswirl). The one
non-obvious rule: the ``Query()`` marker must live **inside** the ``Annotated``
alongside the ``BeforeValidator``. If ``Query()`` is the parameter's *default*
value instead (``x: Annotated[E | None, v] = Query(None)``), FastAPI does not
apply the validator and an empty string 422s before it can be normalized.

A router then declares e.g. ``status: TimelapseStatusFilter = None`` and does
nothing else — the value arrives already validated and normalized.
"""

from typing import Annotated

from fastapi import Query
from models.enum_model import ActivityType, TimelapseStatus
from pydantic import BeforeValidator


def empty_to_none(v: object) -> object:
    """Normalize the dropdown's "All" option ('') to None before enum validation."""
    return None if isinstance(v, str) and v.strip() == "" else v


# ── Closed-set filters: typed by the enum (single source of truth) ──────────
TimelapseStatusFilter = Annotated[
    TimelapseStatus | None,
    BeforeValidator(empty_to_none),
    Query(description="Filter by timelapse status"),
]
ActivityTypeFilter = Annotated[
    ActivityType | None,
    BeforeValidator(empty_to_none),
    Query(description="Filter by activity type"),
]
