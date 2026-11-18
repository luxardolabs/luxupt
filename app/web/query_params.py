"""Typed query-parameter filters — the one standard for optional filters.

A closed-set filter (status / type / …) is typed as its **enum**, so FastAPI
validates it at the boundary (a bad value is a 422, the allowed values land in
OpenAPI) and nothing downstream ever sees a loose string. "No filter" is the
absence of the param or an empty string (the dropdown's "All" option), which a
single ``BeforeValidator`` normalizes to ``None`` **before** validation.

The same empty→None normalization is required for **every** optional *non-str*
query param — ``int``, ``UUID``, ``date``, … — not just enums: an HTML form's
"All"/blank option submits ``?x=`` (present, value ``""``), and any non-str type
422s on ``""`` before the handler runs. ``str`` is exempt (``""`` is valid).
Guarded by ``fw.optional_query_empty_safe`` (LUXARCH ADR-001, playbook enum-typing).

Mirrors the fleet-canonical ``app/core/query_params.py`` (luxswirl). The one
non-obvious rule: the ``Query()`` marker must live **inside** the ``Annotated``
alongside the ``BeforeValidator``. If ``Query()`` is the parameter's *default*
value instead (``x: Annotated[E | None, v] = Query(None)``), FastAPI does not
apply the validator and an empty string 422s before it can be normalized.

A router then declares e.g. ``status: TimelapseStatusFilter = None`` and does
nothing else — the value arrives already validated and normalized.
"""

from datetime import date
from typing import Annotated

from annotated_types import Ge, Le
from fastapi import Query
from pydantic import AfterValidator, BeforeValidator

from app.models.enum_model import ActivityType, TimelapseStatus


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

# ── Optional integer filters: empty ('') → None before int validation ───────
# SQLite stores an INTEGER in 8 bytes: a larger value fails when it is bound, as a 500.
SQLITE_INT_MIN = -(2**63)
SQLITE_INT_MAX = 2**63 - 1

IntFilter = Annotated[
    Annotated[int, Ge(SQLITE_INT_MIN), Le(SQLITE_INT_MAX)] | None,
    BeforeValidator(empty_to_none),
    Query(),
]
"""An optional integer query param (e.g. an interval/user-id filter); '' → None.

Bounded to what SQLite can store, so a larger value is a 422, never a 500 at the query."""

StoredInt = Annotated[int, Ge(SQLITE_INT_MIN), Le(SQLITE_INT_MAX)]
"""An integer path parameter (a row id, a capture timestamp), bounded to what SQLite can store:
a larger one is a 422, never a 500 when the query binds it."""

IntervalSeconds = Annotated[int, Ge(1), Le(SQLITE_INT_MAX)]
"""A capture interval in seconds that a job is created for: at least one second."""

MAX_PAGE = 1_000_000

PageNumber = Annotated[int, Ge(1), Le(MAX_PAGE), Query()]
"""A 1-based page number. Bounded so the offset it becomes, times any page size, fits SQLite."""


def iso_date_or_none(v: str | None) -> str | None:
    """A date value as its canonical `YYYY-MM-DD`; anything that is no date is refused (a 422).

    Every ISO spelling of a day (`20261117`, `2026-W47-2`) is the same day, written one way, so a
    job or a folder it names carries the canonical date."""
    if v is None:
        return None
    return date.fromisoformat(v).isoformat()


IsoDateString = Annotated[
    str | None, BeforeValidator(empty_to_none), AfterValidator(iso_date_or_none)
]
"""An optional ISO date query value (`2026-11-17`), kept as the string the views take; '' → None.

A router names it with its `Query()` (`Annotated[IsoDateString, Query(alias="date")]`), so a value
that is not a date is a 422 naming only the field, never a 500 whose traceback quotes it.
"""

IsoDate = Annotated[str, AfterValidator(iso_date_or_none)]
"""A required ISO date form or query value, kept as the string the views take; anything else is a
422 naming only the field."""

ThumbnailSizeFilter = Annotated[
    Annotated[int, Ge(50), Le(1024)] | None,
    BeforeValidator(empty_to_none),
    Query(),
]
"""Optional thumbnail pixel size, bounded [50, 1024]; '' → None (falls back to default).

The bounds sit on the inner ``int`` (not ``Query(ge=…)``): a ``ge`` on the whole
``int | None`` raises TypeError when ``empty_to_none`` yields ``None`` — the constraint
must never see ``None``.
"""
