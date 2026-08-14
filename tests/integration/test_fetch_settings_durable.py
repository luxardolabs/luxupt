"""Durability lock — save_fetch_settings_durable (db-mutations §5e/§7).

The owned module must COMMIT the settings so a separate reader (the FetchService worker, on
its own session) sees the new values. Bite: removing the module's commit leaves the change
invisible cross-connection (the reader falls back to defaults).

Test-isolation gotcha (FLEET-VERIFICATION-STANDARD): the module mints the *global* async_session,
so we patch it onto the isolated durable_db engine — otherwise the write lands on the wrong engine.
"""

import app.services.core.fetch_settings_durable as durable_mod
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.services.core.settings_core_service import SettingsCoreService

Maker = async_sessionmaker[AsyncSession]


async def test_settings_committed_for_a_separate_reader(
    durable_db: Maker, monkeypatch
) -> None:
    # The owned module's async_session -> the isolated test engine.
    monkeypatch.setattr(durable_mod, "async_session", durable_db)

    await durable_mod.save_fetch_settings_durable({"max_retries": 7})

    # A separate connection sees the committed value (only committed rows cross connections).
    async with durable_db() as reader:
        settings = await SettingsCoreService(reader).get_fetch_settings()
        assert settings.max_retries == 7
