"""Durability lock — SettingsCoreService.save_fetch_settings_durable (db-mutations §5e/§7).

The owned helper must COMMIT the settings so a separate reader (the FetchService worker, on
its own session) sees the new values. Bite: break the write (the crud update) and the change
is invisible cross-connection (the reader falls back to the old value).

Test-isolation (FLEET-VERIFICATION-STANDARD): the helper opens the app's real session owner,
``get_db_context``. ``durable_db`` is the app's own session maker in a ``real_commits`` test, so
the helper's session commits for real on the run's database and is exercised unpatched through
its public seam: nothing private is reached.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.core.settings_core_service import SettingsCoreService

Maker = async_sessionmaker[AsyncSession]


async def test_settings_committed_for_a_separate_reader(durable_db: Maker) -> None:
    async with durable_db() as s:
        await SettingsCoreService(s).save_fetch_settings_durable({"max_retries": 7})

    # A separate connection sees the committed value (only committed rows cross connections).
    async with durable_db() as reader:
        settings = await SettingsCoreService(reader).get_fetch_settings()
        assert settings.max_retries == 7
