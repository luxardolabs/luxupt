"""Durability lock — SettingsCoreService.save_fetch_settings_durable (db-mutations §5e/§7).

The owned helper must COMMIT the settings so a separate reader (the FetchService worker, on
its own session) sees the new values. Bite: removing the commit leaves the change invisible
cross-connection (the reader falls back to defaults).

Test-isolation gotcha (FLEET-VERIFICATION-STANDARD): the helper opens the *global* session owner
(get_db_context), so it is patched (by dotted path, no private-module import) onto the isolated
durable_db engine. The stand-in deliberately does NOT commit, so the helper's own commit is what
makes the row cross the connection — that keeps the bite local: delete the commit, this goes red.
"""

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.core.settings_core_service import SettingsCoreService

Maker = async_sessionmaker[AsyncSession]


async def test_settings_committed_for_a_separate_reader(
    durable_db: Maker, monkeypatch
) -> None:
    # The owned helper's get_db_context -> the isolated test engine (dotted-path patch avoids
    # a cross-package import of the private _fetch_settings_durable module).
    @asynccontextmanager
    async def _owned_session():
        async with durable_db() as s:
            yield s

    monkeypatch.setattr(
        "app.services.core._fetch_settings_durable.get_db_context", _owned_session
    )

    async with durable_db() as s:
        await SettingsCoreService(s).save_fetch_settings_durable({"max_retries": 7})

    # A separate connection sees the committed value (only committed rows cross connections).
    async with durable_db() as reader:
        settings = await SettingsCoreService(reader).get_fetch_settings()
        assert settings.max_retries == 7
