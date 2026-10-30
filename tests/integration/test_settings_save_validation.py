"""A settings save refuses a value the settings read model would refuse.

The pages read settings through validated DTOs (FetchSettingsRead, CameraRead). The save paths
stored raw form values, bounded only by the HTML input's min/max, so one out-of-range save turned
every page that reads it into a 500, the settings panel that could repair it included. An
adversarial pass reproduced it; each case below pairs a refused value with an accepted control.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera_model import Camera
from app.models.enum_model import CaptureMethod
from app.web.main import app

FETCH_FORM = {
    "enabled": "true",
    "intervals": "60",
    "default_capture_method": "auto",
    "default_rtsp_quality": "high",
    "max_retries": "3",
    "retry_delay": "2",
    "request_timeout": "30",
    "rate_limit": "10",
    "rate_limit_buffer": "0.8",
    "min_offset_seconds": "2",
    "max_offset_seconds": "15",
    "camera_refresh_interval": "300",
    "high_quality_snapshots": "true",
    "rtsp_output_format": "png",
    "png_compression_level": "6",
    "rtsp_capture_timeout": "15",
}
PAGES = ("cameras.cameras_page", "cameras.fetch_settings_panel", "system.system_page")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("png_compression_level", "6"),
        ("png_compression_level", "12"),
        ("rtsp_output_format", "png"),
        ("rtsp_output_format", "jpeg-xl-lossless"),
    ],
)
async def test_fetch_settings_save_keeps_every_page_rendering(
    auth_client: AsyncClient, field: str, value: str
) -> None:
    await auth_client.post(
        app.url_path_for("cameras.save_fetch_settings"),
        data={**FETCH_FORM, field: value},
    )
    for name in PAGES:
        page = await auth_client.get(app.url_path_for(name))
        assert page.status_code < 500, (name, page.status_code)


@pytest.mark.parametrize("rtsp_quality", ["high", "x" * 17])
async def test_camera_settings_save_keeps_the_cameras_page_rendering(
    auth_client: AsyncClient, db: AsyncSession, rtsp_quality: str
) -> None:
    db.add(
        Camera(
            camera_id="save-validation-cam",
            name="Save Validation Cam",
            safe_name="save_validation_cam",
            is_active=True,
            is_connected=True,
            capture_method=CaptureMethod.AUTO,
            rtsp_quality="high",
        )
    )
    await db.flush()
    await auth_client.post(
        app.url_path_for(
            "cameras.save_camera_settings", camera_id="save-validation-cam"
        ),
        data={
            "capture_method": "auto",
            "rtsp_quality": rtsp_quality,
            "is_active": "true",
        },
    )
    page = await auth_client.get(app.url_path_for("cameras.cameras_page"))
    assert page.status_code < 500, page.status_code
