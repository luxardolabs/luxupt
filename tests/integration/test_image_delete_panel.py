"""The image delete panel works once images exist, and never deletes everything at once.

The route smoke requests it with no captures, where the preview's list never renders.
"""

import html
import re
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capture_model import Capture
from app.models.enum_model import CaptureMethod, CaptureStatus
from app.services.core.capture_cleanup_core_service import (
    CaptureCleanupCoreService,
    UnfilteredDeleteError,
)

DAY_ONE = date(2026, 1, 1)
DAY_TWO = date(2026, 1, 2)


@pytest.fixture
async def captures(db: AsyncSession) -> None:
    for timestamp, day, interval in (
        (1, DAY_ONE, 60),
        (2, DAY_ONE, 300),
        (3, DAY_TWO, 60),
    ):
        db.add(
            Capture(
                camera_id="delete-cam",
                camera_safe_name="delete_cam",
                timestamp=timestamp,
                capture_datetime=datetime(day.year, day.month, day.day, tzinfo=UTC),
                capture_date=day,
                interval=interval,
                status=CaptureStatus.SUCCESS,
                capture_method=CaptureMethod.API,
                file_size=1024,
            )
        )
    await db.flush()


async def _left(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(Capture)) or 0


async def test_the_panel_renders_with_images_and_offers_no_delete_yet(
    auth_client: AsyncClient, captures: None
) -> None:
    response = await auth_client.get("/images/delete")

    assert response.status_code == 200
    assert "3 images" in response.text
    assert "hx-delete" not in response.text
    assert "data-confirm-text" not in response.text
    assert 'id="date-field"' in response.text
    assert 'id="interval-field"' in response.text


async def test_an_unfiltered_preview_offers_no_delete(
    auth_client: AsyncClient, captures: None
) -> None:
    response = await auth_client.get("/images/delete/preview")

    assert response.status_code == 200
    assert "data-confirm-text" not in response.text


async def test_a_filtered_preview_offers_the_delete(
    auth_client: AsyncClient, captures: None
) -> None:
    response = await auth_client.get(
        "/images/delete/preview", params={"date": DAY_ONE.isoformat()}
    )

    assert response.status_code == 200
    assert "2 images" in response.text
    assert f"/images/delete?date={DAY_ONE.isoformat()}" in response.text


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"camera": "", "date": "", "interval": ""},
        {"interval": "0"},
    ],
)
async def test_a_delete_that_narrows_nothing_is_refused(
    auth_client: AsyncClient, db: AsyncSession, captures: None, params: dict[str, str]
) -> None:
    response = await auth_client.delete("/images/delete", params=params)

    assert response.status_code == 400
    assert "Nothing deleted" in response.text
    assert await _left(db) == 3


async def test_a_filtered_delete_leaves_the_rest_and_renders(
    auth_client: AsyncClient, db: AsyncSession, captures: None
) -> None:
    url = await _delete_url(auth_client, date=DAY_ONE.isoformat())

    response = await auth_client.delete(url)

    assert response.status_code == 200
    assert "Deletion Complete" in response.text
    assert await _left(db) == 1


def _capture(camera_id: str, timestamp: int, day: date) -> Capture:
    return Capture(
        camera_id=camera_id,
        camera_safe_name=camera_id.replace("-", "_"),
        timestamp=timestamp,
        capture_datetime=datetime(day.year, day.month, day.day, tzinfo=UTC),
        capture_date=day,
        interval=60,
        status=CaptureStatus.SUCCESS,
        capture_method=CaptureMethod.API,
        file_size=1024,
    )


async def _delete_url(client: AsyncClient, **params: str) -> str:
    preview = await client.get("/images/delete/preview", params=params)
    found = re.search(r"/images/delete\?[^\"']+", preview.text)
    assert found, preview.text[:500]
    return html.unescape(found.group(0))


@pytest.mark.parametrize("arrival", [100, 2, 0], ids=["newer", "same", "backfilled"])
async def test_a_delete_is_refused_if_a_frame_arrived_after_its_preview(
    auth_client: AsyncClient, db: AsyncSession, captures: None, arrival: int
) -> None:
    url = await _delete_url(auth_client, date=DAY_ONE.isoformat())
    db.add(_capture("delete-cam", arrival, DAY_ONE))
    await db.flush()

    response = await auth_client.delete(url)

    assert response.status_code == 400
    assert "changed since this preview" in response.text
    assert await _left(db) == 4


async def test_a_delete_is_refused_when_different_frames_reach_the_same_count(
    auth_client: AsyncClient, db: AsyncSession, captures: None
) -> None:
    url = await _delete_url(auth_client, camera="delete-cam")
    first = await db.scalar(select(Capture).where(Capture.timestamp == 1))
    assert first is not None
    await db.delete(first)
    db.add(_capture("delete-cam", 100, DAY_TWO))
    await db.flush()

    response = await auth_client.delete(url)

    assert response.status_code == 400
    assert await _left(db) == 3


async def test_the_panel_selects_do_not_take_the_page_toolbars_ids(
    auth_client: AsyncClient, captures: None
) -> None:
    panel = await auth_client.get("/images/delete")

    for name in ("camera", "date", "interval"):
        assert f'id="delete-{name}"' in panel.text
        assert f'name="{name}"' in panel.text
        assert f'id="{name}"' not in panel.text


async def test_a_delete_with_no_count_is_refused(
    auth_client: AsyncClient, db: AsyncSession, captures: None
) -> None:
    response = await auth_client.delete(
        "/images/delete", params={"date": DAY_ONE.isoformat()}
    )

    assert response.status_code == 400
    assert await _left(db) == 3


async def test_replaying_a_delete_removes_nothing_new(
    auth_client: AsyncClient, db: AsyncSession, captures: None
) -> None:
    url = await _delete_url(auth_client, date=DAY_ONE.isoformat())
    await auth_client.delete(url)
    db.add(_capture("delete-cam", 100, DAY_ONE))
    await db.flush()

    second = await auth_client.delete(url)

    assert second.status_code == 400
    assert await _left(db) == 2


async def test_a_replay_matching_the_count_again_is_the_only_repeat_that_deletes(
    auth_client: AsyncClient, db: AsyncSession, captures: None
) -> None:
    url = await _delete_url(auth_client, date=DAY_TWO.isoformat())

    assert (await auth_client.delete(url)).status_code == 200
    assert (await auth_client.delete(url)).status_code == 400
    assert await _left(db) == 2


async def test_a_stale_date_falls_back_to_all_dates_like_its_dropdown(
    auth_client: AsyncClient, db: AsyncSession, captures: None
) -> None:
    db.add(_capture("other-cam", 50, DAY_TWO))
    await db.flush()

    response = await auth_client.get(
        "/images/delete/preview",
        params={"camera": "other-cam", "date": DAY_ONE.isoformat()},
    )

    assert "1 images" in response.text
    assert "No images match" not in response.text
    assert "hx-delete" not in response.text
    assert "data-confirm-text" not in response.text


async def test_the_camera_dropdown_follows_the_preview(
    auth_client: AsyncClient, captures: None
) -> None:
    response = await auth_client.get(
        "/images/delete/preview", params={"camera": "delete-cam"}
    )

    assert 'id="camera-field" hx-swap-oob="innerHTML:#camera-field"' in response.text
    assert re.search(r'value="delete-cam"\s+selected', response.text)


async def test_changing_a_filter_disarms_the_delete_button(
    auth_client: AsyncClient, captures: None
) -> None:
    panel = await auth_client.get("/images/delete")
    preview = await auth_client.get(
        "/images/delete/preview", params={"date": DAY_ONE.isoformat()}
    )

    assert "getElementById(&#39;delete-action&#39;)" in panel.text or (
        "getElementById('delete-action')" in panel.text
    )
    assert 'id="delete-action"' in preview.text
    assert 'hx-sync="this:replace"' in panel.text
    assert 'id="camera-field"' in panel.text


async def test_the_refusal_is_styled_as_an_error(
    auth_client: AsyncClient, captures: None
) -> None:
    response = await auth_client.delete("/images/delete")

    assert "border-danger-500" in response.text


@pytest.mark.parametrize("params", [{}, {"interval": 0}])
async def test_the_core_service_refuses_an_unfiltered_delete(
    db: AsyncSession, captures: None, params: dict[str, int]
) -> None:
    with pytest.raises(UnfilteredDeleteError):
        await CaptureCleanupCoreService(db).delete_by_filters(**params)

    assert await _left(db) == 3
