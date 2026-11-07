"""A failed RTSP capture keeps ffmpeg's diagnosis and loses the stream's address.

A Protect stream URL opens the camera with no login, and the capture's error reaches every signed-in
user through the activity log and the cameras page.
"""

import asyncio
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.clients.camera_manager import CameraManager, CameraManagerSettings
from app.utils.ffmpeg import redact_stream_address

HOST = "nvr-console.invalid"
ALIAS = "aBcDeFgHiJkLmNoP"
STREAM_URL = f"rtsps://{HOST}:7441/{ALIAS}?enableSrtp"
STDERR = (
    f"[tls @ 0x55] Failed to resolve hostname {HOST}: Name or service not known\n"
    f"[tcp @ 0x56] Connection to tcp://{HOST}:7441?timeout=0 failed: Connection refused\n"
    f"[rtsp @ 0x57] method DESCRIBE failed: 404 Not Found\n"
    f"{STREAM_URL}: Server returned 404 Not Found\n"
    f"Input #0, rtsp, from '{STREAM_URL}':\n"
    f"[tls @ 0x58] Unrecognized host alias {ALIAS}\n"
    "Unsupported codec hevc\n"
)


def _assert_no_address(text: str) -> None:
    assert HOST not in text
    assert ALIAS not in text
    assert STREAM_URL not in text


def test_every_form_of_the_address_is_removed() -> None:
    redacted = redact_stream_address(STDERR, STREAM_URL)

    _assert_no_address(redacted)
    assert "Connection refused" in redacted
    assert "404 Not Found" in redacted
    assert "[rtsp @ 0x57]" in redacted


def test_a_url_ffmpeg_printed_differently_is_removed() -> None:
    redacted = redact_stream_address(
        "Opening rtsp://other.invalid:7447/abc for reading", STREAM_URL
    )

    assert redacted == "Opening rtsp://<redacted> for reading"


def test_a_mixed_case_host_is_removed() -> None:
    url = "rtsps://NVR-Console.invalid:7441/alias"

    redacted = redact_stream_address(
        "Failed to resolve hostname NVR-Console.invalid: not known. "
        "Seen on NVR-Console.invalid again",
        url,
    )

    assert "console" not in redacted.lower()


def test_a_redirect_target_loses_its_address() -> None:
    redacted = redact_stream_address(
        "Status 302: Redirecting to rtmp://other.invalid/otheralias\n"
        "Failed to resolve hostname other.invalid: not known",
        STREAM_URL,
    )

    assert "other" not in redacted


def test_an_unresolved_host_with_a_space_is_removed() -> None:
    redacted = redact_stream_address(
        "Failed to resolve hostname bad host.invalid: Name not known", STREAM_URL
    )

    assert redacted == "Failed to resolve hostname <redacted>: Name not known"


def test_a_long_token_does_not_stall_the_event_loop() -> None:
    started = time.monotonic()

    redact_stream_address("a-" * 50_000, STREAM_URL)

    assert time.monotonic() - started < 1.0


def test_an_unparseable_url_keeps_the_diagnosis() -> None:
    url = "rtsps://[nvr.invalid:7441/x"

    redacted = redact_stream_address(f"{url}: Connection refused", url)

    assert redacted == "<stream-url>: Connection refused"


def test_a_host_inside_a_longer_address_is_left_alone() -> None:
    assert redact_stream_address(
        "peer 110.0.0.10 refused", "rtsps://10.0.0.1:7441/alias"
    ) == ("peer 110.0.0.10 refused")


def test_text_with_no_address_is_unchanged() -> None:
    assert redact_stream_address("Conversion failed!", STREAM_URL) == (
        "Conversion failed!"
    )


class _FailingFfmpeg:
    returncode = 1

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", STDERR.encode()


async def test_a_failed_capture_stores_and_logs_no_address(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fake_exec(*_: object, **__: object) -> _FailingFfmpeg:
        return _FailingFfmpeg()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    manager = CameraManager(
        CameraManagerSettings(
            base_url=f"https://{HOST}/proxy/protect/integration/v1",
            api_key="k",
            verify_ssl=False,
            request_timeout=5,
            rate_limit=10,
            rate_limit_buffer=0.8,
            min_offset_seconds=1,
            max_offset_seconds=30,
            camera_refresh_interval=300,
        )
    )
    manager.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )

    async def stream_url(*_: object, **__: object) -> str:
        return STREAM_URL

    monkeypatch.setattr(manager, "get_rtsps_url", stream_url)
    camera = SimpleNamespace(
        id="cam", name="Cam", safe_name="cam", is_connected=True, state="CONNECTED"
    )

    with caplog.at_level(logging.DEBUG):
        result = await manager.capture_snapshot_rtsp(
            camera,
            str(tmp_path / "frame.png"),
            60,
            1_700_000_000,
            quality="high",
            output_format="png",
            png_compression_level=6,
            capture_timeout=5,
        )
    await manager.client.aclose()

    assert result.success is False
    assert result.error_message is not None
    _assert_no_address(result.error_message)
    assert "Connection refused" in result.error_message
    assert "Enhanced (H.265)" in result.error_message
    _assert_no_address(caplog.text)
