"""Direct httpx client for UniFi Protect's private API.

Used for endpoints the public Integration API (X-API-KEY) does not expose:

- POST /api/auth/login                                                 (session cookies)
- GET  /proxy/protect/api/cameras/{id}/recording-snapshot?ts={ms}     (historical JPEG)
- GET  /proxy/protect/api/video/export?camera=...&start=...&end=...    (recorded MP4)
- GET  /proxy/protect/api/cameras/{id}/snapshot                       (live JPEG, full res)

`recording-snapshot` returns native channel-0 recording resolution — the result
LuxUPT's RTSP+ffmpeg path was built to deliver because Ubiquiti's public API is
resolution-capped on most camera models.

The client maintains a single httpx.AsyncClient with a cookie jar; on 401 it
re-logs in once and retries. It is intended to be long-lived (one instance per
app, injected via deps), not constructed per-request.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import urllib3
from logging_config import get_logger

logger = get_logger(__name__)

# Progress callbacks for streaming downloads.
# Args: (current_bytes, total_bytes_or_None). Must be async.
ProgressCallback = Callable[[int, int | None], Awaitable[None]]


class ProtectAuthError(RuntimeError):
    pass


class ProtectRequestError(RuntimeError):
    pass


class ProtectClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool = False,
        timeout: float = 30.0,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not username or not password:
            raise ValueError("username and password are required for private-API access")

        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"base_url must include scheme and host (got: {base_url!r})")
        self.host = f"{parsed.scheme}://{parsed.netloc}"

        self.username = username
        self.password = password
        self.verify_ssl = bool(verify_ssl)
        self.timeout = timeout

        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        self._client: httpx.AsyncClient | None = None
        self._logged_in = False
        self._login_lock = asyncio.Lock()

    async def __aenter__(self) -> "ProtectClient":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                verify=self.verify_ssl,
                timeout=httpx.Timeout(self.timeout, read=None),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
                headers={"User-Agent": "LuxUPT/1.1"},
                follow_redirects=True,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._logged_in = False

    async def _login(self) -> None:
        """Log in via /api/auth/login; safe against concurrent callers.

        Holds an asyncio.Lock so a burst of concurrent fetches doesn't hammer
        Protect with N parallel logins (which Protect rate-limits with HTTP 429).
        """
        async with self._login_lock:
            if self._logged_in:
                # Another task already logged in while we were waiting
                return
            assert self._client is not None
            url = f"{self.host}/api/auth/login"
            resp = await self._client.post(
                url,
                json={"username": self.username, "password": self.password},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                raise ProtectAuthError(f"login failed: HTTP {resp.status_code} - {resp.text[:200]}")
            self._logged_in = True
            logger.info("Protect login successful", extra={"host": self.host, "user": self.username})

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue a request, logging in on first call and once on 401."""
        await self.start()
        if not self._logged_in:
            await self._login()

        url = f"{self.host}{path}"
        assert self._client is not None
        resp = await self._client.request(method, url, **kwargs)
        if resp.status_code == 401:
            logger.info("Got 401, re-logging in", extra={"path": path})
            self._logged_in = False
            await self._login()
            resp = await self._client.request(method, url, **kwargs)
        return resp

    @staticmethod
    def _to_js_ms(dt: datetime) -> int:
        return int(dt.timestamp() * 1000)

    async def get_all_camera_recording_ranges(self) -> dict[str, tuple[datetime, datetime]]:
        """Return {camera_id: (oldest, newest)} for every camera in one bootstrap call.

        Walks `bootstrap.cameras[].stats.video.recordingStart/End` (Unix ms)
        and converts to local-time datetimes. One ~161KB round-trip covers
        the whole NVR's cameras instead of N per-camera calls.
        """
        resp = await self._request("GET", "/proxy/protect/api/bootstrap", headers={"Accept": "application/json"})
        if resp.status_code >= 400:
            raise ProtectRequestError(
                f"bootstrap fetch failed: HTTP {resp.status_code} - {resp.text[:200]}"
            )
        data = resp.json()
        ranges: dict[str, tuple[datetime, datetime]] = {}
        for cam in data.get("cameras", []):
            cid = cam.get("id")
            video = (cam.get("stats") or {}).get("video") or {}
            start_ms = video.get("recordingStart")
            end_ms = video.get("recordingEnd")
            if not (cid and start_ms and end_ms):
                continue
            ranges[cid] = (
                datetime.fromtimestamp(start_ms / 1000).astimezone(),
                datetime.fromtimestamp(end_ms / 1000).astimezone(),
            )
        return ranges

    async def historical_snapshot(self, camera_id: str, ts: datetime) -> bytes:
        """Pull a JPEG from the recording stream at the given timestamp.

        Snaps to nearest GOP keyframe (~5s on most Tylephony cameras). Returned
        bytes are byte-identical for the same `ts` (deterministic).
        """
        path = f"/proxy/protect/api/cameras/{camera_id}/recording-snapshot"
        resp = await self._request("GET", path, params={"ts": self._to_js_ms(ts)}, headers={"Accept": "image/jpeg"})
        if resp.status_code >= 400:
            raise ProtectRequestError(
                f"recording-snapshot failed for {camera_id} at {ts.isoformat()}: "
                f"HTTP {resp.status_code} - {resp.text[:200]}"
            )
        if not resp.headers.get("content-type", "").startswith("image/"):
            raise ProtectRequestError(
                f"recording-snapshot returned non-image content-type: {resp.headers.get('content-type')!r}"
            )
        return resp.content

    async def historical_video(
        self,
        camera_id: str,
        start: datetime,
        end: datetime,
        output_path: Path,
        *,
        channel: int = 0,
        progress_callback: ProgressCallback | None = None,
        chunk_size: int = 65536,
    ) -> None:
        """Stream a recorded MP4 for a range to disk.

        NOTE: do NOT pass `fps=N&type=timelapse` here — Protect's server-side
        timelapse renderer can't complete within the server's wall-time cutoff
        on this hardware. Use this for raw video export (Phase 2 event-manager
        use case) where Protect streams the on-disk MP4 directly (~45 MB/s).
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        path = "/proxy/protect/api/video/export"
        params: dict[str, Any] = {
            "camera": camera_id,
            "start": self._to_js_ms(start),
            "end": self._to_js_ms(end),
            "channel": channel,
        }

        await self.start()
        if not self._logged_in:
            await self._login()
        assert self._client is not None

        url = f"{self.host}{path}"
        async with self._client.stream("GET", url, params=params, headers={"Accept": "video/mp4"}) as resp:
            if resp.status_code == 401:
                await resp.aclose()
                self._logged_in = False
                await self._login()
                async with self._client.stream("GET", url, params=params, headers={"Accept": "video/mp4"}) as resp2:
                    await self._stream_to_file(resp2, output_path, chunk_size, progress_callback)
                return
            if resp.status_code >= 400:
                body = await resp.aread()
                raise ProtectRequestError(
                    f"video/export failed for {camera_id}: HTTP {resp.status_code} - {body[:200]!r}"
                )
            await self._stream_to_file(resp, output_path, chunk_size, progress_callback)

    @staticmethod
    async def _stream_to_file(
        resp: httpx.Response,
        output_path: Path,
        chunk_size: int,
        progress_callback: ProgressCallback | None,
    ) -> None:
        total = int(resp.headers.get("content-length", 0)) or None
        current = 0
        with output_path.open("wb") as f:
            async for chunk in resp.aiter_bytes(chunk_size):
                f.write(chunk)
                current += len(chunk)
                if progress_callback is not None:
                    await progress_callback(current, total)
