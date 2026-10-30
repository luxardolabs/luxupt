"""Durably persist fetch settings in an OWNED transaction (db-mutations §5e).

The FetchService worker reloads settings from its OWN session, so a settings change must be
COMMITTED before ``sync_cameras()`` reads it — a flush on the request session is invisible to
another connection. This helper mints its own session and commits, so the calling service
stays transaction-agnostic (fw.no_redundant_commit). Private to this package; callers reach it
through ``SettingsCoreService.save_fetch_settings_durable``. Declared in
``[allowlist].non_request_files`` as a transaction owner.
"""

from typing import Any

from app.crud.fetch_settings_crud import fetch_settings_crud
from app.db.database import get_db_context
from app.models.fetch_settings_model import FetchSettings


async def save_fetch_settings_durable(settings: dict[str, Any]) -> FetchSettings:
    """Save fetch settings and commit in an owned transaction, so a separate reader
    (the FetchService worker) sees the new values. Returns the saved row."""
    async with get_db_context() as db:
        saved = await fetch_settings_crud.update_settings(db, obj_in=settings)
        await db.commit()
    return saved
