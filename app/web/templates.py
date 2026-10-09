"""The one shared Jinja ``templates`` instance (FLEET-LAYOUT-STANDARD §2).

``create_app`` publishes it on ``app.state.templates``; routers receive it through
``TemplatesDep`` in ``app.web.deps``.
"""

import os
from datetime import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import StrictUndefined

from app.core import config
from app.web.template_filters import register_filters
from app.web.template_helpers import paginated_url

# Templates live at the fleet-canonical app/templates/web/ (app-wide, a sibling of static/;
# email/llm would be peer subdirs). __file__ is app/web/templates.py, so go up to app/.
_TEMPLATES_PATH = Path(__file__).parent.parent / "templates" / "web"

templates = Jinja2Templates(directory=str(_TEMPLATES_PATH))
# StrictUndefined: a missing/renamed/typo'd template variable RAISES instead of rendering
# an empty string. Jinja's default silently renders "" -- a dropped view->template value or
# a stale context key then looks fine and busts nothing (fw.jinja_strict_undefined).
templates.env.undefined = StrictUndefined

register_filters(templates)

# partials/nav.html reads `user` on every page that extends the base layout, so it is
# a GLOBAL, not a per-route key. Declaring the default here clears it everywhere at
# once; a route with a real user overrides it via its own context.
templates.env.globals["user"] = None

templates.env.globals.update(
    {
        "config": config,
        "datetime": datetime,
        "paginated_url": paginated_url,
        # Cache-bust token for first-party static assets (fw.static_assets_cache_busted):
        # the OCI revision (short git SHA), stamped as BUILD_COMMIT in the image
        # (cache-busting playbook — one value shared with org.opencontainers.image.revision).
        "static_version": os.getenv("BUILD_COMMIT", "dev"),
        "len": len,
        "enumerate": enumerate,
        "range": range,
        "max": max,
        "min": min,
        "round": round,
        "int": int,
        "str": str,
        "float": float,
        "dev_mode": config.LOGGING_LEVEL == "DEBUG",
    }
)
