"""The System area's tab bar, shared by its Status, Users and Components pages.

Each tab's link is resolved from its route name (fw.urls_from_route_names), so the three pages
that show the bar carry no copy of the paths. Components is a dev-only page, shown only when the
app runs at DEBUG, the same test the templates' `dev_mode` global makes.
"""

from app.core import config
from app.services.views._camera_options import PathFor


def system_tabs(path: PathFor) -> list[dict[str, str]]:
    """The tab bar's entries, in display order, each with its finished `href`."""
    tabs = [
        {
            "id": "status",
            "label": "Status",
            "href": str(path("system.system_page")),
            "icon_name": "server",
        },
        {
            "id": "users",
            "label": "Users",
            "href": str(path("system.users_page")),
            "icon_name": "users",
        },
    ]
    if config.LOG_LEVEL == "DEBUG":
        tabs.append(
            {
                "id": "components",
                "label": "Components",
                "href": str(path("system.components_page")),
                "icon_name": "swatch",
            }
        )
    return tabs
