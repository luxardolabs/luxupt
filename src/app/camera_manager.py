"""UniFi Protect camera communication, discovery, snapshot capture, and rate limiting."""

import asyncio
import os
import tempfile
import time as time_module
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import urllib3
from logging_config import get_logger
from PIL import Image
from utils import async_fs

# Module logger
logger = get_logger(__name__)


@dataclass
class CaptureResult:
    """Result of a snapshot capture operation."""

    success: bool
    camera_id: str
    camera_safe_name: str
    timestamp: int
    interval: int
    file_path: str | None = None
    file_size: int | None = None
    capture_method: str | None = None  # "api" or "rtsp"
    error_message: str | None = None
    capture_duration_ms: int | None = None


@dataclass
class Camera:
    """Data class representing a camera."""

    id: str
    name: str
    state: str
    mac: str
    is_connected: bool
    is_recording: bool
    model_key: str = "camera"
    video_mode: str = "default"
    hdr_type: str = "auto"
    supports_full_hd_snapshot: bool = False
    has_hdr: bool = False
    has_mic: bool = False
    has_speaker: bool = False
    smart_detect_types: list[str] | None = None

    @classmethod
    def from_api_response(cls, camera_data: dict[str, Any]) -> "Camera":
        """Create Camera instance from API response data."""
        # Extract feature flags
        feature_flags = camera_data.get("featureFlags", {})

        return cls(
            id=camera_data.get("id", ""),
            name=camera_data.get("name", ""),
            state=camera_data.get("state", ""),
            mac=camera_data.get("mac", ""),
            model_key=camera_data.get("modelKey", "camera"),
            video_mode=camera_data.get("videoMode", "default"),
            hdr_type=camera_data.get("hdrType", "auto"),
            is_connected=camera_data.get("state") == "CONNECTED",
            is_recording=camera_data.get("isRecording", False),
            supports_full_hd_snapshot=feature_flags.get("supportFullHdSnapshot", False),
            has_hdr=feature_flags.get("hasHdr", False),
            has_mic=feature_flags.get("hasMic", False),
            has_speaker=feature_flags.get("hasSpeaker", False),
            smart_detect_types=feature_flags.get("smartDetectTypes", []),
        )

    @property
    def safe_name(self) -> str:
        """Return a filesystem-safe version of the camera name."""
        return self.name.replace(" ", "_").replace("/", "_").replace("\\", "_")


def calculate_consecutive_offsets(cameras: list[Camera], offset_seconds: int) -> dict[str, int]:
    """
    Calculate consecutive slot offsets for cameras based on UUID sort order.

    Instead of hash-based sparse distribution, this packs cameras into
    consecutive slots. Cameras are sorted by UUID for deterministic ordering
    (same camera always gets same slot position).

    Args:
        cameras: List of cameras to assign slots to
        offset_seconds: Seconds between each slot

    Returns:
        Dict mapping camera_id to offset in seconds
    """
    if not cameras or offset_seconds <= 0:
        return {cam.id: 0 for cam in cameras}

    # Sort cameras by UUID for deterministic ordering
    sorted_cameras = sorted(cameras, key=lambda c: c.id)

    # Assign consecutive slots starting at slot 1 (offset = offset_seconds)
    # Slot 0 would be offset 0, but we start at slot 1 for a small initial buffer
    offsets = {}
    for idx, camera in enumerate(sorted_cameras):
        slot = idx + 1  # Start at slot 1
        offsets[camera.id] = slot * offset_seconds

    return offsets


@dataclass
class CameraManagerSettings:
    """Settings for CameraManager - populated from database FetchSettings."""

    # API connection (required)
    base_url: str
    api_key: str
    verify_ssl: bool
    request_timeout: int

    # Rate limiting
    rate_limit: int
    rate_limit_buffer: float

    # Camera distribution
    min_offset_seconds: int
    max_offset_seconds: int

    # Camera refresh
    camera_refresh_interval: int

    # RTSPS URL cache TTL (seconds) - can default since not in FetchSettings
    rtsps_url_cache_ttl: int = 3600

    @property
    def effective_rate_limit(self) -> float:
        """Get rate limit adjusted by buffer."""
        return self.rate_limit * self.rate_limit_buffer

    def get_json_headers(self) -> dict[str, str]:
        """Get headers for JSON API requests."""
        return {
            "x-api-key": self.api_key,
            "Accept": "application/json",
        }

    def get_image_headers(self) -> dict[str, str]:
        """Get headers for image requests."""
        return {
            "x-api-key": self.api_key,
            "Accept": "image/jpeg",
        }


class CameraManager:
    """Manages camera discovery and snapshot capture."""

    def __init__(self, settings: CameraManagerSettings) -> None:
        self.settings = settings
        self.client: httpx.AsyncClient
        self.cameras: list[Camera] = []
        self.last_camera_refresh: datetime | None = None

        # Distribution settings — recalculated when camera count changes
        self._locked_total_cameras = 0
        self._locked_use_distribution = False
        self._locked_optimal_offset = 0

        # Cached consecutive offset assignments (recalculated when cameras change)
        self._camera_offsets: dict[str, int] = {}

        # RTSPS URL cache: {camera_id: {"url": str, "quality": str, "created_at": datetime}}
        self._rtsps_cache: dict[str, dict[str, Any]] = {}

        # Disable SSL warnings if verification is disabled
        if not self.settings.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    async def __aenter__(self) -> "CameraManager":
        """Async context manager entry."""
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
        timeout = httpx.Timeout(self.settings.request_timeout)

        self.client = httpx.AsyncClient(
            verify=self.settings.verify_ssl,
            limits=limits,
            timeout=timeout,
            headers={"User-Agent": "LuxUPT/1.1"},
        )

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Async context manager exit."""
        if hasattr(self, "client"):
            await self.client.aclose()

    def update_settings(self, settings: CameraManagerSettings) -> None:
        """Update settings from database values."""
        self.settings = settings

    def should_use_camera_distribution(self, camera_count: int) -> bool:
        """Determine if camera distribution should be used based on camera count."""
        # Always use distribution with multiple cameras - each camera fires at its
        # assigned time slot within the interval, not sequentially blocking each other
        return camera_count > 1

    def calculate_optimal_offset_seconds(self, camera_count: int) -> int:
        """Calculate optimal offset seconds based on camera count and settings."""
        # Use the configured min offset - cameras are spaced this many seconds apart
        return self.settings.min_offset_seconds

    def calculate_effective_concurrent_limit(self) -> int:
        """Calculate effective concurrent camera limit based on rate limit."""
        return max(1, int(self.settings.effective_rate_limit))

    async def refresh_cameras(self, force: bool = False) -> list[Camera]:
        """
        Refresh the list of cameras from the API.
        """
        now = datetime.now()

        # Check if we need to refresh
        if (
            not force
            and self.last_camera_refresh
            and self.cameras
            and (now - self.last_camera_refresh).total_seconds() < self.settings.camera_refresh_interval
        ):
            return self.cameras

        try:
            url = f"{self.settings.base_url}/cameras"

            response = await self.client.get(url, headers=self.settings.get_json_headers())
            response.raise_for_status()
            cameras_data = response.json()

            # Convert API response to Camera objects
            all_cameras = [Camera.from_api_response(cam_data) for cam_data in cameras_data]

            self.cameras = all_cameras
            self.last_camera_refresh = now

            logger.info(
                "Camera discovery complete",
                extra={"camera_count": len(all_cameras)},
            )

            # Log all discovered camera names for debugging
            if all_cameras:
                camera_names = [camera.name for camera in all_cameras]
                logger.debug("Available cameras", extra={"cameras": camera_names})

                # Log camera details
                for camera in all_cameras:
                    logger.debug(
                        "Camera status",
                        extra={
                            "camera": camera.name,
                            "connected": camera.is_connected,
                            "state": camera.state,
                            "model": camera.model_key,
                            "hd_support": camera.supports_full_hd_snapshot,
                        },
                    )

                # Recalculate distribution when camera count changes
                camera_count = len(all_cameras)
                if camera_count != self._locked_total_cameras:
                    if self._locked_total_cameras > 0:
                        logger.info(
                            "Camera count changed, recalculating distribution",
                            extra={"old": self._locked_total_cameras, "new": camera_count},
                        )
                    self._locked_total_cameras = camera_count
                    self._locked_use_distribution = self.should_use_camera_distribution(camera_count)
                    self._locked_optimal_offset = self.calculate_optimal_offset_seconds(camera_count)

                    # Calculate consecutive slot offsets (sorted by UUID)
                    if self._locked_use_distribution:
                        self._camera_offsets = calculate_consecutive_offsets(all_cameras, self._locked_optimal_offset)
                    else:
                        self._camera_offsets = {}

                    logger.info(
                        "Distribution settings updated",
                        extra={
                            "total_cameras": self._locked_total_cameras,
                            "distribution_enabled": self._locked_use_distribution,
                            "offset_seconds": (self._locked_optimal_offset if self._locked_use_distribution else None),
                        },
                    )

                # Log camera distribution information using LOCKED settings
                if self._locked_use_distribution:
                    logger.info(
                        "Camera distribution enabled",
                        extra={
                            "cameras": self._locked_total_cameras,
                            "offset_seconds": self._locked_optimal_offset,
                            "rate_limit": self.settings.rate_limit,
                            "effective_rate_limit": self.settings.effective_rate_limit,
                            "concurrent_limit": self.calculate_effective_concurrent_limit(),
                        },
                    )
                else:
                    logger.info(
                        "Camera distribution disabled",
                        extra={"cameras": self._locked_total_cameras},
                    )

            return self.cameras

        except httpx.RequestError as e:
            logger.error("Failed to fetch cameras", extra={"error": str(e)})
            raise
        except Exception as e:
            logger.error("Unexpected error fetching cameras", extra={"error": str(e)})
            raise

    async def get_cameras(self, force_refresh: bool = False) -> list[Camera]:
        """
        Get the list of cameras, refreshing if necessary.

        Args:
            force_refresh: Force refresh from API

        Returns:
            List of Camera objects
        """
        if not self.cameras or force_refresh:
            await self.refresh_cameras(force=force_refresh)

        return self.cameras

    def _write_and_verify_snapshot(self, output_path: str, data: bytes, min_size: int) -> int:
        """Write snapshot data and return file size. Returns 0 if too small/missing."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(data)
        if os.path.exists(output_path) and os.path.getsize(output_path) > min_size:
            return os.path.getsize(output_path)
        return 0

    async def capture_snapshot(
        self,
        camera: Camera,
        output_path: str,
        interval: int,
        timestamp: int,
        retry_count: int,
        *,
        high_quality: bool,
    ) -> CaptureResult:
        """
        Capture a snapshot from the specified camera.

        Args:
            camera: Camera to capture from
            output_path: Path to save the image
            interval: Interval in seconds (for logging)
            timestamp: Unix timestamp for the capture
            retry_count: Current retry attempt number
            high_quality: Whether to request high quality snapshot (from database settings)
        """
        start_time = time_module.time()

        # Base result for failures
        def make_result(success: bool, file_size: int | None = None, error: str | None = None) -> CaptureResult:
            """Build a CaptureResult with elapsed time from the enclosing capture call."""
            duration_ms = int((time_module.time() - start_time) * 1000)
            return CaptureResult(
                success=success,
                camera_id=camera.id,
                camera_safe_name=camera.safe_name,
                timestamp=timestamp,
                interval=interval,
                file_path=output_path if success else None,
                file_size=file_size,
                capture_method="api",
                error_message=error,
                capture_duration_ms=duration_ms,
            )

        if not camera.is_connected:
            logger.debug(
                "Skipping disconnected camera",
                extra={"camera": camera.name, "interval": interval, "state": camera.state},
            )
            return make_result(False, error=f"Camera not connected (state: {camera.state})")

        try:
            url = f"{self.settings.base_url}/cameras/{camera.id}/snapshot"

            # Build query parameters - only use highQuality if camera supports it
            params = {}
            if high_quality and camera.supports_full_hd_snapshot:
                params["highQuality"] = "true"
                quality_note = "HQ"
            else:
                quality_note = "STD"

            # Log the request we're about to make
            logger.debug("Requesting snapshot", extra={"camera": camera.name, "interval": interval})

            response = await self.client.get(url, headers=self.settings.get_image_headers(), params=params)

            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "")

                if content_type.startswith("image/"):
                    # Write image data and verify in a single thread dispatch
                    file_size = await asyncio.to_thread(
                        self._write_and_verify_snapshot, output_path, response.content, 1000
                    )

                    if file_size > 0:
                        logger.debug(
                            "Snapshot captured",
                            extra={
                                "camera": camera.name,
                                "interval": interval,
                                "quality": quality_note,
                                "file_size_bytes": file_size,
                                "path": output_path,
                            },
                        )
                        return make_result(True, file_size=file_size)
                    else:
                        error_msg = "Image file too small or missing"
                        logger.error(
                            "Capture failed - file too small",
                            extra={"camera": camera.name, "interval": interval, "error": error_msg},
                        )
                        return make_result(False, error=error_msg)
                else:
                    error_msg = f"Invalid content type: {content_type}"
                    logger.error(
                        "Capture failed - invalid content type",
                        extra={"camera": camera.name, "interval": interval, "content_type": content_type},
                    )
                    return make_result(False, error=error_msg)
            else:
                # Try to get error details
                try:
                    error_data = response.json()
                    error_msg = error_data.get("message", "Unknown error")
                except Exception:
                    error_msg = f"HTTP {response.status_code}"

                logger.error(
                    "Snapshot request failed",
                    extra={
                        "camera": camera.name,
                        "interval": interval,
                        "status_code": response.status_code,
                        "error": error_msg,
                    },
                )
                return make_result(False, error=error_msg)

        except httpx.TimeoutException:
            error_msg = "Timeout"
            logger.error("Capture timeout", extra={"camera": camera.name, "interval": interval})
            return make_result(False, error=error_msg)
        except httpx.RequestError as e:
            error_msg = "Network error"
            logger.error("Capture network error", extra={"camera": camera.name, "interval": interval, "error": str(e)})
            return make_result(False, error=error_msg)
        except Exception as e:
            error_msg = "Capture failed"
            logger.error(
                "Capture unexpected error", extra={"camera": camera.name, "interval": interval, "error": str(e)}
            )
            return make_result(False, error=error_msg)

    # =========================================================================
    # RTSP CAPTURE METHODS
    # =========================================================================

    async def get_rtsps_url(self, camera: Camera, quality: str = "high") -> str | None:
        """
        Get or create an RTSPS stream URL for the camera.

        Args:
            camera: Camera to get stream URL for
            quality: Stream quality ("high", "medium", "low")

        Returns:
            RTSPS URL string or None if failed
        """
        # Check cache first
        cache_key = camera.id
        if cache_key in self._rtsps_cache:
            cached = self._rtsps_cache[cache_key]
            # Cache is valid for configured TTL
            cache_age = (datetime.now() - cached["created_at"]).total_seconds()
            if cache_age < self.settings.rtsps_url_cache_ttl and cached["quality"] == quality:
                return str(cached["url"])

        try:
            url = f"{self.settings.base_url}/cameras/{camera.id}/rtsps-stream"

            response = await self.client.post(
                url,
                headers=self.settings.get_json_headers(),
                json={"qualities": [quality]},
            )

            if response.status_code == 200:
                streams = response.json()
                rtsps_url = streams.get(quality)

                if rtsps_url:
                    # Cache the URL
                    rtsps_url_str = str(rtsps_url)
                    self._rtsps_cache[cache_key] = {
                        "url": rtsps_url_str,
                        "quality": quality,
                        "created_at": datetime.now(),
                    }
                    logger.debug(
                        "Got RTSPS URL",
                        extra={"camera": camera.name, "quality": quality},
                    )
                    return rtsps_url_str
                else:
                    logger.error(
                        "No stream URL in response",
                        extra={"camera": camera.name, "quality": quality},
                    )
                    return None
            else:
                logger.error(
                    "Failed to get RTSPS URL",
                    extra={"camera": camera.name, "status_code": response.status_code},
                )
                return None

        except Exception as e:
            logger.error(
                "Error getting RTSPS URL",
                extra={"camera": camera.name, "error": str(e)},
            )
            return None

    async def capture_snapshot_rtsp(
        self,
        camera: Camera,
        output_path: str,
        interval: int,
        timestamp: int,
        *,
        quality: str,
        output_format: str,
        png_compression_level: int,
        capture_timeout: int,
    ) -> CaptureResult:
        """
        Capture a snapshot from the camera using RTSP stream and FFmpeg.

        This method extracts a high-quality frame from the camera's RTSP stream,
        providing much higher resolution than the API snapshot endpoint.

        Args:
            camera: Camera to capture from
            output_path: Path to save the image
            interval: Interval in seconds (for logging)
            timestamp: Unix timestamp for the capture
            quality: RTSP quality setting ("high", "medium", "low") from database
            output_format: Image format ("png" or "jpg") from database
            png_compression_level: PNG compression 0-9 (0=fast/large, 9=slow/small, 6=balanced)
            capture_timeout: Timeout in seconds for RTSP capture from database

        Returns:
            CaptureResult with success status and capture details
        """
        rtsp_quality = quality
        start_time = time_module.time()

        def make_result(success: bool, file_size: int | None = None, error: str | None = None) -> CaptureResult:
            """Build a CaptureResult with elapsed time from the enclosing RTSP capture call."""
            duration_ms = int((time_module.time() - start_time) * 1000)
            return CaptureResult(
                success=success,
                camera_id=camera.id,
                camera_safe_name=camera.safe_name,
                timestamp=timestamp,
                interval=interval,
                file_path=output_path if success else None,
                file_size=file_size,
                capture_method="rtsp",
                error_message=error,
                capture_duration_ms=duration_ms,
            )

        if not camera.is_connected:
            logger.debug(
                "Skipping disconnected camera",
                extra={"camera": camera.name, "interval": interval, "state": camera.state},
            )
            return make_result(False, error=f"Camera not connected (state: {camera.state})")

        # Get RTSPS URL
        rtsps_url = await self.get_rtsps_url(camera, rtsp_quality)
        if not rtsps_url:
            error_msg = "Could not get RTSPS URL"
            logger.error(
                "RTSP capture failed - no URL",
                extra={"camera": camera.name, "interval": interval},
            )
            return make_result(False, error=error_msg)

        # Ensure output directory exists
        await async_fs.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Build FFmpeg command for high-quality frame extraction
        # Uses I-frame selection and deinterlacing for best results
        ffmpeg_command = [
            "ffmpeg",
            "-rtsp_transport",
            "tcp",
            "-rtbufsize",
            "15M",
            "-max_delay",
            "5000000",
            "-fflags",
            "+genpts+discardcorrupt",
            "-i",
            rtsps_url,
            "-t",
            "0.5",  # Get 0.5s of video to find a good frame
            # Filter: deinterlace, select I-frames or scene changes, light sharpen
            "-vf",
            "yadif=0:-1:0,select='if(eq(pict_type,I),1,gt(scene,0.1))',setpts=N/FRAME_RATE/TB,unsharp=3:3:1",
            "-vsync",
            "0",
            "-frames:v",
            "1",
            "-update",
            "1",  # Required for single image output
        ]

        # Add format-specific options
        if output_format == "png":
            ffmpeg_command.extend(
                [
                    "-pix_fmt",
                    "rgb24",
                    "-pred",
                    "mixed",
                    "-compression_level",
                    str(png_compression_level),
                ]
            )
        else:  # jpg
            ffmpeg_command.extend(
                [
                    "-q:v",
                    "2",  # High quality JPEG (1-31, lower is better)
                ]
            )

        ffmpeg_command.extend(
            [
                "-y",  # Overwrite output
                output_path,
            ]
        )

        try:
            start_time = time_module.perf_counter()

            process = await asyncio.create_subprocess_exec(
                *ffmpeg_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=capture_timeout)
                encode_duration_ms = (time_module.perf_counter() - start_time) * 1000

                exists, file_size = await async_fs.file_exists_and_size(output_path)
                if process.returncode == 0 and exists:
                    # Validate minimum file size (5KB for high-res images)
                    if file_size > 5000:
                        logger.debug(
                            "RTSP snapshot captured",
                            extra={
                                "camera": camera.name,
                                "interval": interval,
                                "file_size_bytes": file_size,
                                "encode_ms": round(encode_duration_ms, 1),
                                "format": output_format,
                                "compression_level": png_compression_level if output_format == "png" else None,
                                "path": output_path,
                            },
                        )
                        return make_result(True, file_size=file_size)
                    else:
                        error_msg = f"Image too small: {file_size} bytes"
                        logger.error(
                            "RTSP capture failed - image too small",
                            extra={"camera": camera.name, "interval": interval, "file_size_bytes": file_size},
                        )
                        await asyncio.to_thread(lambda: os.remove(output_path) if os.path.exists(output_path) else None)
                        return make_result(False, error=error_msg)
                else:
                    stderr_text = stderr.decode("utf-8", errors="ignore") if stderr else ""
                    error_msg = f"FFmpeg error: {stderr_text[:100]}" if stderr_text else "FFmpeg failed"
                    logger.error(
                        "RTSP capture failed - FFmpeg error",
                        extra={"camera": camera.name, "interval": interval, "error": error_msg},
                    )
                    return make_result(False, error=error_msg)

            except asyncio.TimeoutError:
                error_msg = "Timeout"
                logger.error(
                    "RTSP capture timeout",
                    extra={"camera": camera.name, "interval": interval, "timeout_seconds": capture_timeout},
                )
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
                return make_result(False, error=error_msg)

        except Exception as e:
            error_msg = "RTSP capture failed"
            logger.error(
                "RTSP capture error",
                extra={"camera": camera.name, "interval": interval, "error": str(e)},
            )
            return make_result(False, error=error_msg)

    # =========================================================================
    # CAPABILITY DETECTION
    # =========================================================================

    async def detect_camera_capabilities(self, camera: Camera) -> dict[str, Any]:
        """
        Detect capture capabilities for a camera by testing both API and RTSP methods.

        This performs test captures to determine the maximum resolution available
        from each capture method, then recommends the best method.

        Args:
            camera: Camera to test

        Returns:
            Dictionary with:
                - api_max_resolution: str like "1920x1080" or None if failed
                - rtsp_max_resolution: str like "3840x2160" or None if failed
                - recommended_method: "api" or "rtsp" based on results
                - supports_full_hd_snapshot: bool from camera feature flags
        """
        result: dict[str, str | bool | None] = {
            "api_max_resolution": None,
            "rtsp_max_resolution": None,
            "recommended_method": None,
            "supports_full_hd_snapshot": camera.supports_full_hd_snapshot,
        }

        timestamp = int(datetime.now().timestamp())

        # Test API capture
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                api_path = tmp.name

            api_result = await self.capture_snapshot(camera, api_path, 0, timestamp, 0, high_quality=True)

            if api_result.success:
                api_exists = await asyncio.to_thread(os.path.exists, api_path)
                if api_exists:

                    def get_image_dimensions(path: str) -> tuple[int, int]:
                        """Read width and height from an API snapshot image."""
                        with Image.open(path) as img:
                            return img.size

                    width, height = await asyncio.to_thread(get_image_dimensions, api_path)
                    result["api_max_resolution"] = f"{width}x{height}"
                    logger.info(
                        "API capture test complete",
                        extra={"camera": camera.name, "width": width, "height": height},
                    )

            # Cleanup
            await asyncio.to_thread(lambda: os.remove(api_path) if os.path.exists(api_path) else None)

        except Exception as e:
            logger.warning("API capture test failed", extra={"camera": camera.name, "error": str(e)})

        # Test RTSP capture
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                rtsp_path = tmp.name

            rtsp_result = await self.capture_snapshot_rtsp(
                camera,
                rtsp_path,
                0,
                timestamp,
                quality="high",
                output_format="jpg",  # Use jpg for quick test captures
                png_compression_level=6,  # Not used for jpg but required param
                capture_timeout=15,
            )

            if rtsp_result.success:
                rtsp_exists = await asyncio.to_thread(os.path.exists, rtsp_path)
                if rtsp_exists:

                    def get_rtsp_image_dimensions(path: str) -> tuple[int, int]:
                        """Read width and height from an RTSP snapshot image."""
                        with Image.open(path) as img:
                            return img.size

                    width, height = await asyncio.to_thread(get_rtsp_image_dimensions, rtsp_path)
                    result["rtsp_max_resolution"] = f"{width}x{height}"
                    logger.info(
                        "RTSP capture test complete",
                        extra={"camera": camera.name, "width": width, "height": height},
                    )

            # Cleanup
            await asyncio.to_thread(lambda: os.remove(rtsp_path) if os.path.exists(rtsp_path) else None)

        except Exception as e:
            logger.warning("RTSP capture test failed", extra={"camera": camera.name, "error": str(e)})

        # Determine recommended method
        api_res = result["api_max_resolution"]
        rtsp_res = result["rtsp_max_resolution"]

        if isinstance(api_res, str) and isinstance(rtsp_res, str):
            # Parse resolutions and compare
            api_pixels = self._parse_resolution(api_res)
            rtsp_pixels = self._parse_resolution(rtsp_res)

            if api_pixels >= rtsp_pixels:
                # API provides same or better resolution - prefer it (faster)
                result["recommended_method"] = "api"
            else:
                # RTSP provides higher resolution
                result["recommended_method"] = "rtsp"
        elif isinstance(api_res, str):
            result["recommended_method"] = "api"
        elif isinstance(rtsp_res, str):
            result["recommended_method"] = "rtsp"

        logger.info(
            "Capability detection complete",
            extra={
                "camera": camera.name,
                "api_resolution": api_res,
                "rtsp_resolution": rtsp_res,
                "recommended_method": result["recommended_method"],
            },
        )

        return result

    def _parse_resolution(self, resolution: str) -> int:
        """Parse resolution string like '1920x1080' and return total pixels."""
        try:
            width, height = resolution.split("x")
            return int(width) * int(height)
        except (ValueError, AttributeError):
            return 0

    async def get_effective_capture_method(
        self,
        camera: Camera,
        camera_settings: dict[str, Any] | None = None,
        default_method: str = "auto",
    ) -> str:
        """
        Determine which capture method to use for a camera.

        Priority:
        1. Per-camera setting from database (if not "auto")
        2. Auto-detection based on camera capabilities
        3. Provided default_method (from fetch_settings.default_capture_method)

        Args:
            camera: Camera dataclass from API
            camera_settings: Optional dict with capture_method, recommended_method fields
            default_method: Default capture method from database settings

        Returns:
            "api" or "rtsp"
        """
        # Check per-camera setting
        if camera_settings:
            method = camera_settings.get("capture_method", "auto")
            if method in ("api", "rtsp"):
                return str(method)

            # Auto mode - use recommendation if available
            if method == "auto":
                recommended = camera_settings.get("recommended_method")
                if recommended in ("api", "rtsp"):
                    return str(recommended)

        # Fallback to capability-based selection
        if camera.supports_full_hd_snapshot:
            return "api"

        # Use provided default from database
        if default_method in ("api", "rtsp"):
            return default_method

        # Final fallback for "auto" default - prefer API
        return "api"
