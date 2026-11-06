"""A camera's capture counts are incremented in one statement, so concurrent captures keep every count."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.camera_crud import camera_crud
from app.models.camera_model import Camera


async def _camera(db: AsyncSession) -> Camera:
    camera = Camera(camera_id="cam-counts", name="Counts", safe_name="counts")
    db.add(camera)
    await db.flush()
    return camera


async def test_a_count_another_writer_stored_is_not_overwritten(
    db: AsyncSession,
) -> None:
    camera = await _camera(db)
    assert camera.total_captures == 0
    # The session's loaded copy still says 0 while the row says 5, as it does when another
    # worker counted captures after this session read the camera.
    await db.execute(
        update(Camera)
        .where(Camera.camera_id == "cam-counts")
        .values(total_captures=5)
        .execution_options(synchronize_session=False)
    )

    counted = await camera_crud.increment_captures(db, "cam-counts")

    assert counted is not None
    assert counted.total_captures == 6
    assert counted.failed_captures == 0
    assert counted.last_capture_at is not None


async def test_a_failed_capture_counts_in_both_totals(db: AsyncSession) -> None:
    await _camera(db)

    counted = await camera_crud.increment_captures(db, "cam-counts", success=False)

    assert counted is not None
    assert counted.total_captures == 1
    assert counted.failed_captures == 1


async def test_counting_keeps_a_pending_edit_to_the_same_camera(
    db: AsyncSession,
) -> None:
    camera = await _camera(db)
    camera.name = "Renamed"

    counted = await camera_crud.increment_captures(db, "cam-counts")

    assert counted is not None
    assert counted.name == "Renamed"
    assert counted.total_captures == 1


async def test_an_unknown_camera_counts_nothing(db: AsyncSession) -> None:
    assert await camera_crud.increment_captures(db, "no-such-camera") is None
