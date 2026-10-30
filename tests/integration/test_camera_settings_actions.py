"""The camera settings panel's Detect button posts to that camera's detect route.

The button passed `"/cameras/{{ camera.camera_id }}/detect"` to the button macro as a Jinja string
literal. A string literal is not a template, so the braces were rendered verbatim and the button
posted to a path no camera has. The view now resolves the detect URL by route name.
"""

from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera_model import Camera
from app.models.enum_model import CaptureMethod
from app.web.main import app

CAMERA_ID = "settings-actions-camera-uuid"


async def test_detect_button_posts_to_this_cameras_detect_route(
    auth_client: AsyncClient, db: AsyncSession
) -> None:
    db.add(
        Camera(
            camera_id=CAMERA_ID,
            name="Settings Actions Cam",
            safe_name="Settings_Actions_Cam",
            is_active=True,
            is_connected=True,
            capture_method=CaptureMethod.AUTO,
            rtsp_quality="high",
            enabled_intervals=[60],
            first_discovered_at=datetime.now(UTC),
        )
    )
    await db.flush()

    resp = await auth_client.get(
        app.url_path_for("cameras.camera_settings_panel", camera_id=CAMERA_ID)
    )

    assert resp.status_code == 200
    assert "{{" not in resp.text
    detect = app.url_path_for("cameras.detect_camera_capabilities", camera_id=CAMERA_ID)
    assert f'hx-post="{detect}"' in resp.text
