# luxarch:route-links asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit route-links`.
"""The runtime net under fw.urls_from_route_names: template links and route names, against the LIVE
route table.

Save as `tests/test_route_links.py` and set the two EDIT THIS lines. luxarch reads the route table
statically; this imports the app and walks the one it actually serves, so a route registered where a
static reader cannot follow (a loop, a factory, a plugin) is seen too. Two checks:

- every literal `href` / `action` / `formaction` / `hx-*` path in a web template is served by a route
  for the method it sends (an `href` is a GET, a form's `action` its `method=`, `hx-post` a POST), slash
  for slash (`/x/` against a route `/x` is a 307 and a lost POST body, so it fails too);
- every route name resolves to ONE path within its Starlette namespace (a sub-app mounted with
  `name="blog"` resolves as `blog:<name>`). `url_path_for` returns the first route with a name, so a name
  shared by a web page and an API endpoint sends the page's links to the API, with no error. FastAPI's
  own docs routes (`openapi`, `swagger_ui_html`, …) are left out: an app does not name them.

It is the net while literal paths burn down (`luxarch --playbook url-in-view`), not the fix: the fix is
building each link from its route name in the view service.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# --- EDIT THIS: how your app object is imported (the thing that owns the route table). ---
APP_IMPORT = "app.web.main:app"  # "package.module:attribute"
# --- EDIT THIS: the web template roots, relative to the repo root (not email or LLM templates). ---
TEMPLATE_ROOTS: tuple[str, ...] = ("app/templates/web",)

_LINK = re.compile(
    r"""\b(href|action|formaction|hx-(?:get|post|put|patch|delete|push-url|replace-url))\s*=\s*(["'])(/[^"'{]*)\2""",
    re.IGNORECASE,
)
_FORM_METHOD = re.compile(r"""\bmethod\s*=\s*["']?(\w+)""", re.IGNORECASE)
_FASTAPI_OWN = frozenset(
    {"openapi", "swagger_ui_html", "swagger_ui_redirect", "redoc_html"}
)
_PARAM = re.compile(r"\{(\w+)(?::(\w+))?\}")
_REPO = Path(__file__).resolve().parents[1]


def _app() -> Any:
    module, _, attr = APP_IMPORT.partition(":")
    return getattr(importlib.import_module(module), attr)


def _walk(
    routes: Any, prefix: str = "", ns: str = ""
) -> Iterator[tuple[str, str | None, frozenset[str]]]:
    """(full path, namespaced name, methods) for every route, through mounts and included routers."""
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            # FastAPI 0.141+ keeps an included router whole (a private `_IncludedRouter`): the
            # include's prefix is on its context, the router's own prefix is already in its routes'
            # paths. Older releases flatten the routes onto the app with full paths. An unread shape
            # makes a link read as dead (loud), never as served.
            pre = str(getattr(getattr(r, "include_context", None), "prefix", "") or "")
            yield from _walk(getattr(inner, "routes", []), prefix + pre, ns)
            continue
        path = prefix + str(getattr(r, "path", "") or "")
        children = getattr(r, "routes", None)
        if children:
            mount = getattr(
                r, "name", None
            )  # a NAMED mount namespaces its routes' names
            yield from _walk(children, path, f"{ns}{mount}:" if mount else ns)
        elif hasattr(r, "endpoint"):
            name = getattr(r, "name", None)
            methods = frozenset(getattr(r, "methods", None) or {"GET"})
            yield (
                path,
                (f"{ns}{name}" if name and name not in _FASTAPI_OWN else None),
                methods,
            )
        elif hasattr(
            r, "app"
        ):  # a mount with no route table of its own (StaticFiles): serves its prefix
            yield path.rstrip("/") + "/{rest:path}", None, frozenset({"GET"})


def _pattern(path: str) -> re.Pattern[str]:
    parts, last = [], 0
    for m in _PARAM.finditer(path):
        parts.append(re.escape(path[last : m.start()]))
        parts.append(".+" if m.group(2) == "path" else "[^/]+")
        last = m.end()
    parts.append(re.escape(path[last:]))
    return re.compile("".join(parts) + r"\Z")


def _method(attr: str, line: str) -> str:
    attr = attr.lower()
    if attr.startswith("hx-") and attr not in ("hx-push-url", "hx-replace-url"):
        return attr[3:].upper()
    if attr in ("action", "formaction"):
        m = _FORM_METHOD.search(line)
        return m.group(1).upper() if m else "GET"
    return "GET"


def _links() -> Iterator[tuple[str, int, str, str]]:
    for root in TEMPLATE_ROOTS:
        for t in sorted((_REPO / root).rglob("*.html")):
            for i, line in enumerate(t.read_text(encoding="utf-8").splitlines(), 1):
                for m in _LINK.finditer(line):
                    link = m.group(3).split("#", 1)[0].split("?", 1)[0]
                    if link and not link.startswith(("//", "/static/")):
                        yield (
                            str(t.relative_to(_REPO)),
                            i,
                            link,
                            _method(m.group(1), line),
                        )


def test_every_template_link_is_served_by_a_route() -> None:
    served = [(_pattern(p), methods) for p, _, methods in _walk(_app().routes)]
    assert served, f"no routes found on {APP_IMPORT}: fix APP_IMPORT"
    roots = [r for r in TEMPLATE_ROOTS if (_REPO / r).is_dir()]
    assert roots, f"none of TEMPLATE_ROOTS {TEMPLATE_ROOTS} exists: fix TEMPLATE_ROOTS"
    dead = [
        f"{f}:{i} {verb} {link}"
        for f, i, link, verb in _links()
        if not any(p.match(link) and verb in methods for p, methods in served)
    ]
    assert not dead, "template links no route serves (for that method):\n" + "\n".join(
        dead
    )


def test_every_route_name_resolves_to_one_path() -> None:
    by_name: dict[str, set[str]] = {}
    for path, name, _methods in _walk(_app().routes):
        if name:
            by_name.setdefault(name, set()).add(path)
    collisions = {n: sorted(ps) for n, ps in by_name.items() if len(ps) > 1}
    assert not collisions, (
        "route names shared by several paths (url_path_for returns the first):\n"
        + "\n".join(f"{n}: {', '.join(ps)}" for n, ps in sorted(collisions.items()))
    )
