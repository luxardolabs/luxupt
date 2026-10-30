"""Thumbnails and the lightbox image resolve from their route names.

The camera card, image card and lightbox spelled their image paths in the template
(`/images/thumbnail/{{ camera.safe_name }}/...`, `/images/file/...`), a second home for each
route. The lightbox's keyboard-navigation data also read `prev_params` / `next_params`, which no
context supplied, so under StrictUndefined any image with a neighbour failed to render. The
views now build each URL by route name; these tests render them with seeded captures.
"""

from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera_model import Camera
from app.models.capture_model import Capture
from app.models.enum_model import CaptureMethod, CaptureStatus
from app.utils.timezones import business_day
from app.web.main import app

CAMERA_ID = "image-urls-camera-uuid"
SAFE_NAME = "Image_Urls_Cam"
FIRST, SECOND = 1767225600, 1767225660


async def _seed(db: AsyncSession) -> str:
    now = datetime.now(UTC)
    today = business_day()
    db.add(
        Camera(
            camera_id=CAMERA_ID,
            name="Image Urls Cam",
            safe_name=SAFE_NAME,
            is_active=True,
            is_connected=True,
            capture_method=CaptureMethod.AUTO,
            rtsp_quality="high",
            enabled_intervals=[60],
            first_discovered_at=now,
        )
    )
    for ts in (FIRST, SECOND):
        db.add(
            Capture(
                camera_id=CAMERA_ID,
                camera_safe_name=SAFE_NAME,
                timestamp=ts,
                capture_datetime=now,
                capture_date=today,
                interval=60,
                status=CaptureStatus.SUCCESS,
                capture_method=CaptureMethod.API,
                file_path=f"/nonexistent/{SAFE_NAME}/{ts}.jpg",
                file_size=1024,
            )
        )
    await db.flush()
    return today.isoformat()


async def test_camera_card_thumbnail_is_resolved_by_route_name(
    auth_client: AsyncClient, db: AsyncSession
) -> None:
    today = await _seed(db)

    resp = await auth_client.get(app.url_path_for("cameras.cameras_page"))

    assert resp.status_code == 200
    thumb = app.url_path_for(
        "images.get_image_thumbnail",
        camera=SAFE_NAME,
        interval="60",
        capture_date=today,
        timestamp=str(SECOND),
    )
    assert f'src="{thumb}?size=512"' in resp.text


async def test_lightbox_image_and_navigation_are_resolved_by_route_name(
    auth_client: AsyncClient, db: AsyncSession
) -> None:
    await _seed(db)

    resp = await auth_client.get(
        app.url_path_for(
            "images.image_lightbox_content",
            camera_safe_name=SAFE_NAME,
            timestamp=str(FIRST),
        ),
        params={"interval": 60},
    )

    assert resp.status_code == 200
    image = app.url_path_for(
        "images.get_image_file", camera=SAFE_NAME, interval="60", timestamp=str(FIRST)
    )
    assert f'src="{image}"' in resp.text
    neighbour = app.url_path_for(
        "images.image_lightbox_content",
        camera_safe_name=SAFE_NAME,
        timestamp=str(SECOND),
    )
    assert f'data-next="{neighbour}?interval=60"' in resp.text
