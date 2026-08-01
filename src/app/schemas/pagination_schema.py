"""Canonical pagination state — the fleet DTO produced by build_pagination().

The view service computes ``total`` from a count query, calls build_pagination(),
and passes the result into the template as ``pagination``. Templates render it via
the pagination_controls macro; every link is built through the paginated_url helper
(so filters + page ride together). Never compute total_pages / has_prev in the view
or the template — that is what fw.view_pagination flags. (LUXARCH pagination playbook;
DTO shape matches the fleet standard — luxof.life/luxswirl.)
"""

from pydantic import BaseModel


class Pagination(BaseModel):
    """Pagination state for a single rendered page."""

    page: int  # 1-indexed
    per_page: int
    total: int
    total_pages: int  # >= 1 even when total == 0 (avoids "Page 1 of 0")
    range_start: int  # 1-indexed; 0 when total == 0
    range_end: int  # min(page * per_page, total)
    has_prev: bool
    has_next: bool
    prev_page: int | None  # None on the first page
    next_page: int | None  # None on the last page


def build_pagination(*, page: int, per_page: int, total: int) -> Pagination:
    """Construct a Pagination from raw paging inputs.

    Call this once per paginated view-service method — never build the fields by hand
    (fw.view_pagination). ``total`` is the unpaginated row count from a COUNT query.
    """
    if per_page <= 0:
        raise ValueError("per_page must be > 0")
    page = max(1, page)

    total_pages = max(1, -(-total // per_page))  # ceil div, min 1
    has_prev = page > 1
    has_next = page < total_pages

    if total <= 0:
        range_start = 0
        range_end = 0
    else:
        range_start = (page - 1) * per_page + 1
        range_end = min(page * per_page, total)

    return Pagination(
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        range_start=range_start,
        range_end=range_end,
        has_prev=has_prev,
        has_next=has_next,
        prev_page=page - 1 if has_prev else None,
        next_page=page + 1 if has_next else None,
    )
