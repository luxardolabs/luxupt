"""Service-layer test for ActivityCoreService — covers the get_summary CASE
aggregation (Router -> View -> Core -> CRUD, exercised at the Core seam).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import activity_crud
from app.models.enum_model import ActivityType
from app.services.core.activity_core_service import ActivityCoreService


class TestActivityCoreService:
    async def test_summary_counts_by_type(self, db_session: AsyncSession) -> None:
        svc = ActivityCoreService(db_session)
        await activity_crud.log(
            db_session, activity_type=ActivityType.CAPTURE_SUCCESS, message="ok"
        )
        await activity_crud.log(
            db_session, activity_type=ActivityType.CAPTURE_SUCCESS, message="ok2"
        )
        await activity_crud.log(
            db_session, activity_type=ActivityType.CAPTURE_FAILED, message="bad"
        )

        summary = await svc.get_summary(hours=24)

        assert summary.total_events == 3
        assert summary.capture_success_count == 2
        assert summary.capture_failed_count == 1
        assert summary.timelapse_failed_count == 0

    async def test_summary_empty(self, db_session: AsyncSession) -> None:
        summary = await ActivityCoreService(db_session).get_summary(hours=24)
        assert summary.total_events == 0
        assert summary.capture_success_count == 0
