"""Data-layer round-trip for activity_crud — proves the DB test path (temp sqlite +
schema + session fixture) and that the VARCHAR-backed enum column stores/reads as the
ActivityType enum (str_enum), the pattern the fw.enumish_columns guard enforces.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import activity_crud
from app.models.enum_model import ActivityType


class TestActivityCrud:
    async def test_log_then_read_back(self, db_session: AsyncSession) -> None:
        created = await activity_crud.log(
            db_session,
            activity_type=ActivityType.CAPTURE_FAILED,
            message="camera boom",
            interval=60,
            camera_id="cam-1",
        )
        assert created.id is not None

        rows = await activity_crud.get_recent(db_session, limit=10)
        match = next((r for r in rows if r.message == "camera boom"), None)
        assert match is not None
        assert match.interval == 60
        assert match.camera_id == "cam-1"
        # stored VARCHAR reads back as the enum, not a bare string
        assert match.activity_type == ActivityType.CAPTURE_FAILED

    async def test_filter_by_type(self, db_session: AsyncSession) -> None:
        await activity_crud.log(
            db_session, activity_type=ActivityType.CAPTURE_SUCCESS, message="ok"
        )
        await activity_crud.log(
            db_session, activity_type=ActivityType.CAPTURE_FAILED, message="bad"
        )
        failed = await activity_crud.get_recent(
            db_session, activity_types=[ActivityType.CAPTURE_FAILED], limit=10
        )
        assert failed
        assert all(r.activity_type == ActivityType.CAPTURE_FAILED for r in failed)
