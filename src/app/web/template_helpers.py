"""Jinja template helpers registered as globals (see web/main.py).

``paginated_url`` is the single source of truth for every table/pagination URL, so
filters + page always ride together and nothing hand-concatenates a query string
(LUXARCH pagination playbook; mirrors the fleet-canonical helper — luxof.life).
"""

from urllib.parse import urlencode


def paginated_url(
    base_url: str, query_params: dict[str, object] | None = None, **overrides: object
) -> str:
    """Preserve the page's existing query params, apply overrides (page / a filter),
    and strip empty/None so URLs stay clean. Used by every pagination link so the
    active filters survive a page change."""
    params = dict(query_params or {})
    params.update(overrides)
    filtered = {k: str(v) for k, v in params.items() if v is not None and v != ""}
    return f"{base_url}?{urlencode(filtered)}" if filtered else base_url
