"""Durably persist fetch settings in an OWNED transaction (db-mutations §5e).

The FetchService worker reloads settings from its OWN session, so a settings change must be
COMMITTED before ``sync_cameras()`` reads it — a flush on the request session is invisible to
another connection. This module owns its session (mints ``async_session`` and commits), so the
calling view service stays transaction-agnostic (fw.no_redundant_commit). It is a transaction
owner, declared in ``[allowlist].non_request_files``.
"""

from typing import Any

from app.db.connection import async_session
from app.services.core.settings_core_service import SettingsCoreService


async def save_fetch_settings_durable(settings: dict[str, Any]) -> None:
    """Save fetch settings and commit in an owned transaction, so a separate reader
    (the FetchService worker) sees the new values."""
    async with async_session() as db:
        service = SettingsCoreService(db)
        await service.update_fetch_settings(settings)
        await db.commit()
