"""Durability lock — SettingsCoreService.save_fetch_settings_durable (db-mutations §5e/§7).

The owned helper must COMMIT the settings so a separate reader (the FetchService worker, on
its own session) sees the new values. Bite: removing the commit leaves the change invisible
cross-connection (the reader falls back to defaults).

Test-isolation gotcha (FLEET-VERIFICATION-STANDARD): the helper mints the *global* async_session,
so it is patched (by dotted path, no private-module import) onto the isolated durable_db engine.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.core.settings_core_service import SettingsCoreService

Maker = async_sessionmaker[AsyncSession]


async def test_settings_committed_for_a_separate_reader(
    durable_db: Maker, monkeypatch
) -> None:
    # The owned helper's async_session -> the isolated test engine (dotted-path patch avoids
    # a cross-package import of the private _fetch_settings_durable module).
    monkeypatch.setattr(
        "app.services.core._fetch_settings_durable.async_session", durable_db
    )

    async with durable_db() as s:
        await SettingsCoreService(s).save_fetch_settings_durable({"max_retries": 7})

    # A separate connection sees the committed value (only committed rows cross connections).
    async with durable_db() as reader:
        settings = await SettingsCoreService(reader).get_fetch_settings()
        assert settings.max_retries == 7
