"""Durability locks for the writes moved into crud (db-mutations §7, owner-boundary style).

``seed_singleton_settings`` now delegates to each settings crud's ``seed_default``, and a
completed timelapse is written through ``timelapse_crud.create`` with ``TimelapseCreate``
(which gained ``thumbnail_path`` and ``completed_at``). Each lock writes, commits, and reads
back on a SEPARATE session, where only committed rows are visible. Bite: break the write
(the ``db.add`` in ``seed_default``, or drop a field from the schema) and the read-back fails.
"""

from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import timelapse_crud
from app.db.database import seed_singleton_settings
from app.models import BackupSettings, FetchSettings, SchedulerSettings
from app.models.enum_model import TimelapseStatus
from app.models.timelapse_model import Timelapse
from app.schemas.timelapse_schema import TimelapseCreate

Maker = async_sessionmaker[AsyncSession]
SETTINGS = (FetchSettings, SchedulerSettings, BackupSettings)


async def test_seed_creates_every_missing_singleton(durable_db: Maker) -> None:
    async with durable_db() as s:
        for model in SETTINGS:
            await s.execute(delete(model))
        await s.commit()

    await seed_singleton_settings()

    async with durable_db() as reader:
        for model in SETTINGS:
            row = await reader.get(model, 1)
            assert row is not None, f"{model.__name__} singleton not seeded"
        fetch = await reader.get(FetchSettings, 1)
        assert fetch is not None
        assert fetch.intervals == [15, 30, 60, 120, 300]


async def test_seed_never_overwrites_an_existing_row(durable_db: Maker) -> None:
    async with durable_db() as s:
        fetch = await s.get(FetchSettings, 1)
        assert fetch is not None
        fetch.max_retries = 9
        await s.commit()

    await seed_singleton_settings()

    async with durable_db() as reader:
        fetch = await reader.get(FetchSettings, 1)
        assert fetch is not None
        assert fetch.max_retries == 9


async def test_completed_timelapse_record_persists_every_field(
    durable_db: Maker,
) -> None:
    completed_at = datetime(2026, 10, 2, 3, 4, 5, tzinfo=UTC)
    async with durable_db() as s:
        await timelapse_crud.create(
            s,
            obj_in=TimelapseCreate(
                camera_id="",
                camera_safe_name="front_door",
                timelapse_date=date(2026, 10, 1),
                interval=60,
                frame_count=1440,
                frame_rate=30,
                duration_seconds=48.0,
                file_path="/videos/front_door.mp4",
                file_name="front_door.mp4",
                file_size=1234,
                resolution="1920x1080",
                thumbnail_path="/videos/front_door_thumb.jpg",
                status=TimelapseStatus.COMPLETED,
                completed_at=completed_at,
            ),
        )
        await s.commit()

    async with durable_db() as reader:
        row = (
            await reader.execute(
                select(Timelapse).where(Timelapse.camera_safe_name == "front_door")
            )
        ).scalar_one()
        assert row.thumbnail_path == "/videos/front_door_thumb.jpg"
        assert row.completed_at == completed_at
        assert row.status == TimelapseStatus.COMPLETED
