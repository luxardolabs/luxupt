# luxarch:sql-allowlist asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit sql-allowlist`.
"""The one way text the app did not write becomes part of an SQL statement: ``sql_choice``.

A VALUE (a date, a number, a name to match) is a bound parameter (``:name``), never text. An
IDENTIFIER (a column, a sort direction, a table) or a fixed SQL word cannot be bound, so it is
chosen from a set the app wrote: ``sql_choice(value, ALLOWED)`` returns the member of ``ALLOWED``
equal to ``value`` and raises ``ValueError`` for anything else. What it hands on is always one of
the strings written in ``ALLOWED``, never the caller's object.

``fw.no_dynamic_column_sort`` knows this function by its import and by this file being the emitted
copy (adopt it whole; an edited copy is not recognised). ``ALLOWED`` must be a ``frozenset`` of
string literals written at the call, or a constant of the same module assigned that literal once:

    from app.core.sql_allowlist import sql_choice

    SORTABLE = frozenset({"created_at", "name"})


    def page_sql(sort_by: str, order: str) -> TextClause:
        col = sql_choice(sort_by, SORTABLE)
        direction = sql_choice(order, frozenset({"ASC", "DESC"}))
        return text(f"SELECT id FROM items ORDER BY {col} {direction} LIMIT :limit")

Text a model or a client genuinely writes is neither: it runs on a confined connection, proven by
the sql-confinement asserter (``luxarch --emit sql-confinement``). See ``luxarch --playbook
pagination``, "Text-to-SQL".
"""

from __future__ import annotations


def sql_choice(value: object, allowed: frozenset[str]) -> str:
    """The member of ``allowed`` equal to ``value``; ``ValueError`` when there is none."""
    if type(allowed) is not frozenset or not all(type(a) is str for a in allowed):
        raise TypeError("sql_choice: the allowed set must be a frozenset of str")
    if type(value) is str:
        for member in allowed:
            if member == value:
                return member
    raise ValueError(f"sql_choice: {value!r} is not one of {sorted(allowed)}")
