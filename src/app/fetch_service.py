"""Periodic snapshot capture service with rate limiting, retry logic, and scheduling."""

import asyncio
import math
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from camera_manager import Camera as ApiCamera
from camera_manager import (
    CameraManager,
    CameraManagerSettings,
    CaptureResult,
    calculate_consecutive_offsets,
)
from crud import activity_crud, camera_crud, capture_crud, fetch_settings_crud
from db.connection import async_session, init_db
from logging_config import get_logger
from models.activity import ActivityType
from schemas.capture import CaptureCreate
from services.image_service import image_service

# Module logger
logger = get_logger(__name__)


def find_common_aligned_timestamp(intervals: list[int]) -> int:
    """
    Find a timestamp that aligns perfectly with ALL given intervals.

    This ensures that cameras with different intervals (15s, 30s, 60s, etc.)
    all start capturing at the same moment, with their captures staying
    synchronized over time.

    Args:
        intervals: List of capture intervals in seconds (only intervals with cameras)

    Returns:
        Next timestamp that is divisible by LCM of all intervals
    """
    if not intervals:
        return int(time.time()) + 5

    # Calculate LCM of all intervals
    def lcm(a: int, b: int) -> int:
        """Compute the least common multiple of two integers."""
        return abs(a * b) // math.gcd(a, b)

    common_period = intervals[0]
    for interval in intervals[1:]:
        common_period = lcm(common_period, interval)

    now = int(time.time())
    # Find next timestamp divisible by common_period, with small buffer
    next_aligned = ((now // common_period) + 1) * common_period
    return next_aligned


class FetchService:
    """Service for periodically fetching camera snapshots."""

    def __init__(self) -> None:
        self.camera_manager: CameraManager
        self.interval_tasks: dict[int, asyncio.Task] = {}  # interval -> task
        self.running = False
        self.intervals: list[int] = []  # Global intervals from DB

        # Common start timestamp for alignment
        self.common_start_timestamp: int = 0

        # Track current API settings to detect changes
        self._current_base_url: str = ""
        self._current_api_key: str = ""

        logger.info("FetchService initialized")

    async def _load_intervals_from_db(self) -> list[int]:
        """Load all available intervals from database."""
        try:
            async with async_session() as session:
                settings = await fetch_settings_crud.get_settings(session)

                # If no intervals in DB, use defaults
                if not settings.intervals:
                    default_intervals = [15, 30, 60, 120, 300]
                    await fetch_settings_crud.update_settings(session, obj_in={"intervals": default_intervals})
                    await session.commit()
                    logger.info("Initialized intervals in database", extra={"intervals": default_intervals})
                    return default_intervals
                else:
                    return settings.intervals

        except Exception as e:
            logger.warning("Failed to load intervals from database", extra={"error": str(e), "fallback": [60]})
            return [60]

    async def _get_active_intervals(self) -> list[int]:
        """Get intervals that actually have cameras configured.

        Returns only intervals that at least one active camera is using.
        This is used for LCM calculation to avoid waiting for unused intervals.
        """
        active_intervals: set[int] = set()
        try:
            async with async_session() as session:
                cameras = await camera_crud.get_active(session)
                for cam in cameras:
                    if cam.enabled_intervals:
                        active_intervals.update(cam.enabled_intervals)
        except Exception as e:
            logger.warning("Failed to get active intervals", extra={"error": str(e)})

        # If no cameras or no intervals, default to [60]
        if not active_intervals:
            return [60]

        return sorted(active_intervals)

    async def _load_camera_settings(self) -> dict[str, dict[str, Any]]:
        """Load camera settings from database."""
        settings = {}
        try:
            async with async_session() as session:
                cameras = await camera_crud.get_active(session)
                for cam in cameras:
                    settings[cam.camera_id] = {
                        "capture_method": cam.capture_method,
                        "rtsp_quality": cam.rtsp_quality,
                        "enabled_intervals": cam.enabled_intervals,
                        "recommended_method": cam.recommended_method,
                        "is_active": cam.is_active,
                    }
        except Exception as e:
            logger.warning("Failed to load camera settings from database", extra={"error": str(e)})
        return settings

    async def _load_fetch_defaults(self) -> dict[str, Any]:
        """Load default capture settings from database."""
        defaults: dict[str, Any] = {}
        try:
            async with async_session() as session:
                fetch_settings = await fetch_settings_crud.get_settings(session)
                defaults["default_capture_method"] = fetch_settings.default_capture_method
                defaults["default_rtsp_quality"] = fetch_settings.default_rtsp_quality
                defaults["high_quality_snapshots"] = fetch_settings.high_quality_snapshots
                defaults["rtsp_output_format"] = fetch_settings.rtsp_output_format
                defaults["png_compression_level"] = fetch_settings.png_compression_level
                defaults["rtsp_capture_timeout"] = fetch_settings.rtsp_capture_timeout
                defaults["max_retries"] = fetch_settings.max_retries
                defaults["retry_delay"] = fetch_settings.retry_delay
                defaults["camera_refresh_interval"] = fetch_settings.camera_refresh_interval
        except Exception as e:
            logger.error("Failed to load fetch defaults from database", extra={"error": str(e)})
            raise  # Don't continue without database settings
        return defaults

    async def _load_camera_manager_settings(self) -> CameraManagerSettings:
        """Load settings for CameraManager from database."""
        async with async_session() as session:
            fetch_settings = await fetch_settings_crud.get_settings(session)

            return CameraManagerSettings(
                base_url=config.UNIFI_PROTECT_BASE_URL or fetch_settings.base_url or "",
                api_key=config.UNIFI_PROTECT_API_KEY or fetch_settings.api_key or "",
                verify_ssl=(
                    config.UNIFI_PROTECT_VERIFY_SSL if config.UNIFI_PROTECT_BASE_URL else fetch_settings.verify_ssl
                ),
                request_timeout=fetch_settings.request_timeout,
                rate_limit=fetch_settings.rate_limit,
                rate_limit_buffer=fetch_settings.rate_limit_buffer,
                min_offset_seconds=fetch_settings.min_offset_seconds,
                max_offset_seconds=fetch_settings.max_offset_seconds,
                camera_refresh_interval=fetch_settings.camera_refresh_interval,
            )

    async def _get_camera_settings(self) -> dict[str, dict[str, Any]]:
        """Get camera settings from database."""
        return await self._load_camera_settings()

    async def _sync_cameras_to_db(self) -> None:
        """Sync cameras from API to database with default settings for new cameras."""
        try:
            cameras = await self.camera_manager.get_cameras(force_refresh=True)
            logger.info("Syncing cameras to database...")

            async with async_session() as db:
                new_cameras = []
                for camera in cameras:
                    existing = await camera_crud.get_by_camera_id(db, camera.id)

                    camera_data = {
                        "camera_id": camera.id,
                        "name": camera.name,
                        "safe_name": camera.safe_name,
                        "mac": camera.mac,
                        "model_key": camera.model_key,
                        "video_mode": camera.video_mode,
                        "hdr_type": camera.hdr_type,
                        "state": camera.state,
                        "is_connected": camera.is_connected,
                        "is_recording": camera.is_recording,
                        "supports_full_hd_snapshot": camera.supports_full_hd_snapshot,
                        "has_hdr": camera.has_hdr,
                        "has_mic": camera.has_mic,
                        "has_speaker": camera.has_speaker,
                        "smart_detect_types": camera.smart_detect_types,
                    }

                    # Set defaults for new cameras only
                    if not existing:
                        camera_data["first_discovered_at"] = datetime.now()
                        camera_data["is_active"] = True
                        camera_data["capture_method"] = "rtsp"
                        camera_data["rtsp_quality"] = "high"
                        camera_data["enabled_intervals"] = [60]  # Default to 60s only
                        new_cameras.append(camera.name)

                    await camera_crud.upsert_from_dict(db, data=camera_data)

                await db.commit()
                logger.info("Synced cameras to database", extra={"camera_count": len(cameras)})

                if new_cameras:
                    logger.info(
                        "New cameras with defaults",
                        extra={"cameras": new_cameras, "default_interval": 60, "default_method": "auto"},
                    )

                # Run capability detection for new connected cameras (parallel)
                new_connected = [c for c in cameras if c.name in new_cameras and c.is_connected]
                if new_connected:
                    logger.info("Running capability detection", extra={"camera_count": len(new_connected)})

                    async def _detect_one(camera: ApiCamera) -> None:
                        try:
                            capabilities = await self.camera_manager.detect_camera_capabilities(camera)

                            await camera_crud.update_capability_detection(
                                db,
                                camera.id,
                                api_max_resolution=capabilities.get("api_max_resolution"),
                                rtsp_max_resolution=capabilities.get("rtsp_max_resolution"),
                                recommended_method=capabilities.get("recommended_method"),
                            )

                            logger.info(
                                "Detected camera capabilities",
                                extra={
                                    "camera": camera.name,
                                    "api_resolution": capabilities.get("api_max_resolution"),
                                    "rtsp_resolution": capabilities.get("rtsp_max_resolution"),
                                    "recommended_method": capabilities.get("recommended_method"),
                                },
                            )

                        except Exception as e:
                            logger.warning(
                                "Failed to detect capabilities", extra={"camera": camera.name, "error": str(e)}
                            )

                    await asyncio.gather(*[_detect_one(cam) for cam in new_connected])
                    await db.commit()
                    logger.info("Capability detection complete")

        except Exception as e:
            logger.warning("Failed to sync cameras to database", extra={"error": str(e)})

    async def sync_cameras(self) -> int:
        """Trigger camera sync on demand (e.g., after settings change).

        Reloads settings and syncs cameras from API to database.
        Returns number of cameras synced, or -1 on error.
        """
        try:
            # Reload settings and reinitialize camera manager
            cm_settings = await self._load_camera_manager_settings()

            # Close existing camera manager if running
            if hasattr(self, "camera_manager") and self.camera_manager:
                await self.camera_manager.__aexit__(None, None, None)

            # Create new camera manager with fresh settings
            self.camera_manager = CameraManager(cm_settings)
            await self.camera_manager.__aenter__()

            # Sync cameras
            await self._sync_cameras_to_db()

            # Return count
            cameras = await self.camera_manager.get_cameras()
            return len(cameras)

        except Exception as e:
            logger.error("Failed to sync cameras on demand", extra={"error": str(e)})
            return -1

    async def start(self) -> None:
        """Start the fetch service."""
        if self.running:
            logger.debug("Fetch service is already running")
            return

        self.running = True

        # Initialize database
        logger.info("Initializing database for fetch service...")
        await init_db()

        # Load all available intervals from database
        self.intervals = await self._load_intervals_from_db()
        logger.info("Loaded intervals from database", extra={"intervals": self.intervals})

        # Get intervals that actually have cameras - use THESE for LCM
        active_intervals = await self._get_active_intervals()
        logger.info("Active intervals (cameras configured)", extra={"active_intervals": active_intervals})

        # Calculate common aligned timestamp based on ACTIVE intervals only
        self.common_start_timestamp = find_common_aligned_timestamp(active_intervals)
        logger.info(
            "Common start timestamp calculated",
            extra={"timestamp": self.common_start_timestamp, "based_on": active_intervals},
        )

        # Start image service
        await image_service.start()

        # Load CameraManager settings from database
        cm_settings = await self._load_camera_manager_settings()

        # Track current settings for change detection
        self._current_base_url = cm_settings.base_url
        self._current_api_key = cm_settings.api_key

        # Initialize camera manager (works even with empty URL - will fail on actual requests)
        self.camera_manager = CameraManager(cm_settings)
        await self.camera_manager.__aenter__()

        # Try to sync cameras - OK if it fails (no API configured yet)
        try:
            await self._sync_cameras_to_db()
        except Exception as e:
            logger.info("Could not sync cameras (API may not be configured yet)", extra={"error": str(e)})

        # Try to discover cameras (OK if it fails - tasks will retry)
        try:
            cameras = await self.camera_manager.get_cameras(force_refresh=True)
            use_distribution = self.camera_manager.should_use_camera_distribution(len(cameras))
            if use_distribution:
                optimal_offset = self.camera_manager.calculate_optimal_offset_seconds(len(cameras))
                logger.info(
                    "Camera distribution enabled",
                    extra={"camera_count": len(cameras), "offset_seconds": optimal_offset},
                )
            else:
                logger.info("Camera distribution disabled", extra={"camera_count": len(cameras)})
        except Exception as e:
            logger.info("Could not discover cameras (API may not be configured yet)", extra={"error": str(e)})

        try:
            # Start interval tasks (they handle no cameras gracefully)
            for interval in self.intervals:
                task = asyncio.create_task(self._run_interval(interval))
                self.interval_tasks[interval] = task
                logger.info("Started interval task", extra={"interval": interval})

            # Start interval monitor task (checks for changes)
            monitor_task = asyncio.create_task(self._monitor_interval_changes())

            # Wait for all tasks
            all_tasks = list(self.interval_tasks.values()) + [monitor_task]
            await asyncio.gather(*all_tasks, return_exceptions=True)

        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the fetch service."""
        if not self.running:
            return

        self.running = False

        # Cancel all interval tasks
        for interval, task in self.interval_tasks.items():
            if not task.done():
                task.cancel()
                logger.info("Cancelled interval task", extra={"interval": interval})

        # Wait for tasks to complete
        if self.interval_tasks:
            await asyncio.gather(*self.interval_tasks.values(), return_exceptions=True)

        self.interval_tasks.clear()

        # Close camera manager
        if hasattr(self, "camera_manager"):
            await self.camera_manager.__aexit__(None, None, None)

        # Stop image service
        await image_service.stop()

        logger.info("Fetch service stopped")

    async def capture_once(self, timestamp: int, interval: int) -> dict[str, CaptureResult]:
        """
        Perform a one-off capture for all cameras at the specified interval.

        This is used by the CLI test command to verify camera connectivity.

        Args:
            timestamp: Unix timestamp for the capture
            interval: Interval in seconds (for directory structure and filtering)

        Returns:
            Dictionary mapping camera names to CaptureResult
        """
        if not self.camera_manager:
            raise RuntimeError("FetchService not started - call start() first")

        # Get cameras and filter for this interval
        cameras = await self.camera_manager.get_cameras()
        camera_settings = await self._load_camera_settings()
        filtered_cameras = self._filter_cameras_for_interval(cameras, camera_settings, interval)
        connected_cameras = [cam for cam in filtered_cameras if cam.is_connected]

        if not connected_cameras:
            logger.debug("No connected cameras available for capture")
            return {}

        # Load fetch defaults from database
        fetch_defaults = await self._load_fetch_defaults()

        # Determine if we should use distribution
        use_distribution = self.camera_manager.should_use_camera_distribution(len(connected_cameras))

        if use_distribution:
            results = await self._capture_distributed(
                connected_cameras, camera_settings, timestamp, interval, fetch_defaults
            )
        else:
            results = await self._capture_concurrent(
                connected_cameras, camera_settings, timestamp, interval, fetch_defaults
            )

        # Record captures to database
        await self._record_captures_to_db(results)

        return results

    async def _monitor_interval_changes(self) -> None:
        """Monitor for changes to global intervals and API settings, update tasks accordingly."""
        while self.running:
            await asyncio.sleep(config.SETTINGS_RELOAD_INTERVAL)

            if not self.running:
                break

            try:
                # Check for API settings changes first
                cm_settings = await self._load_camera_manager_settings()
                if cm_settings.base_url != self._current_base_url or cm_settings.api_key != self._current_api_key:
                    logger.debug(
                        "API settings changed, reinitializing camera manager",
                        extra={"has_url": bool(cm_settings.base_url), "has_key": bool(cm_settings.api_key)},
                    )

                    # Close existing camera manager
                    if hasattr(self, "camera_manager") and self.camera_manager:
                        await self.camera_manager.__aexit__(None, None, None)

                    # Create new camera manager with fresh settings
                    self.camera_manager = CameraManager(cm_settings)
                    await self.camera_manager.__aenter__()

                    # Update tracked settings
                    self._current_base_url = cm_settings.base_url
                    self._current_api_key = cm_settings.api_key

                    # Sync cameras if API is now configured
                    if cm_settings.base_url and cm_settings.api_key:
                        try:
                            await self._sync_cameras_to_db()
                            cameras = await self.camera_manager.get_cameras(force_refresh=True)
                            logger.info("Cameras discovered after settings change", extra={"count": len(cameras)})

                            # Recalculate timestamp based on active intervals (cameras just synced)
                            active_intervals = await self._get_active_intervals()
                            self.common_start_timestamp = find_common_aligned_timestamp(active_intervals)
                            logger.info(
                                "Recalculated timestamp after camera sync",
                                extra={"timestamp": self.common_start_timestamp, "based_on": active_intervals},
                            )
                        except Exception as e:
                            logger.warning("Could not sync cameras after settings change", extra={"error": str(e)})

                # Load current intervals from database
                new_intervals = await self._load_intervals_from_db()

                # Check for changes
                current_set = set(self.intervals)
                new_set = set(new_intervals)

                added = new_set - current_set
                removed = current_set - new_set

                if added or removed:
                    logger.info("Interval changes detected", extra={"added": list(added), "removed": list(removed)})

                    # Stop removed interval tasks
                    for interval in removed:
                        if interval in self.interval_tasks:
                            task = self.interval_tasks[interval]
                            if not task.done():
                                task.cancel()
                                logger.info("Stopped interval task", extra={"interval": interval})
                            del self.interval_tasks[interval]

                    # Start new interval tasks
                    for interval in added:
                        task = asyncio.create_task(self._run_interval(interval))
                        self.interval_tasks[interval] = task
                        logger.info("Started new interval task", extra={"interval": interval})

                    # Update intervals list
                    self.intervals = new_intervals

                    # Recalculate common start timestamp based on ACTIVE intervals
                    if added or removed:
                        active_intervals = await self._get_active_intervals()
                        self.common_start_timestamp = find_common_aligned_timestamp(active_intervals)
                        logger.info(
                            "Updated common start timestamp",
                            extra={"timestamp": self.common_start_timestamp, "based_on": active_intervals},
                        )

            except Exception as e:
                logger.error("Error monitoring settings changes", extra={"error": str(e)})

    async def _check_fetch_enabled(self) -> bool:
        """Check if fetch is globally enabled in database settings."""
        try:
            async with async_session() as session:
                return await fetch_settings_crud.is_enabled(session)
        except Exception as e:
            logger.warning("Failed to check fetch enabled status", extra={"error": str(e)})
            return True  # Default to enabled if check fails

    async def _run_interval(self, interval: int) -> None:
        """Run capture loop for a specific interval."""
        logger.info("Starting interval capture loop", extra={"interval": interval})

        # ALL intervals use the exact same start timestamp
        next_aligned_ts = self.common_start_timestamp

        # Log alignment status
        if self.common_start_timestamp % interval == 0:
            logger.debug(
                "Starting aligned with timestamp",
                extra={"interval": interval, "timestamp": self.common_start_timestamp},
            )
        else:
            offset = self.common_start_timestamp % interval
            logger.debug(
                "Starting with offset from natural alignment",
                extra={"interval": interval, "offset": offset},
            )

        # Wait for first execution
        sleep_time = next_aligned_ts - time.time()
        if sleep_time > 0:
            logger.debug(
                "First capture scheduled",
                extra={
                    "interval": interval,
                    "sleep_seconds": round(sleep_time, 1),
                    "scheduled_time": datetime.fromtimestamp(next_aligned_ts).strftime("%H:%M:%S"),
                },
            )
            await asyncio.sleep(sleep_time)

        # Track in-flight capture cycles to prevent unbounded task growth
        in_flight: set[asyncio.Task] = set()
        max_in_flight = 2  # Allow current + 1 overlap, skip if further behind

        # Track last enabled check time to avoid checking on every iteration
        last_enabled_check = 0.0

        while self.running:
            # Check if fetch is globally enabled (throttled to SETTINGS_RELOAD_INTERVAL)
            now_time = time.time()
            if now_time - last_enabled_check >= config.SETTINGS_RELOAD_INTERVAL:
                last_enabled_check = now_time
                if not await self._check_fetch_enabled():
                    logger.debug("Fetch disabled, skipping capture", extra={"interval": interval})
                    await asyncio.sleep(config.SETTINGS_RELOAD_INTERVAL)
                    continue

            # Snap to the current aligned timestamp using integer division
            # This is robust against asyncio.sleep overshoot — no exact-second polling needed
            now_ts = int(time.time())
            elapsed = now_ts - self.common_start_timestamp
            current_aligned_ts = self.common_start_timestamp + (elapsed // interval) * interval

            # Only fire if we've reached or passed the next expected timestamp
            if current_aligned_ts < next_aligned_ts:
                # Woke up too early, sleep until target
                sleep_time = next_aligned_ts - time.time()
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                else:
                    await asyncio.sleep(0.1)
                continue

            # Use the aligned timestamp for this capture
            timestamp = next_aligned_ts
            capture_time = datetime.fromtimestamp(timestamp)

            try:
                # Clean up completed tasks
                in_flight = {t for t in in_flight if not t.done()}

                # Guard against falling behind - skip if too many cycles still running
                if len(in_flight) >= max_in_flight:
                    logger.warning(
                        "Capture cycle skipped - previous cycles still running",
                        extra={
                            "interval": interval,
                            "in_flight": len(in_flight),
                            "time": capture_time.strftime("%H:%M:%S"),
                        },
                    )
                    # Log to activity feed so users see it in the dashboard
                    try:
                        async with async_session() as session:
                            await activity_crud.log(
                                session,
                                activity_type=ActivityType.ERROR,
                                message=f"Capture cycle skipped for {interval}s interval - {len(in_flight)} previous cycles still running",
                                interval=interval,
                                details={
                                    "in_flight": len(in_flight),
                                    "time": capture_time.strftime("%H:%M:%S"),
                                },
                            )
                            await session.commit()
                    except Exception:
                        pass  # Don't let activity logging break the interval loop
                else:
                    # Get cameras from API (uses internal caching)
                    api_cameras = await self.camera_manager.get_cameras()

                    # Get camera settings from cache
                    camera_settings = await self._get_camera_settings()

                    # Filter cameras based on per-camera interval settings
                    filtered_cameras = self._filter_cameras_for_interval(api_cameras, camera_settings, interval)

                    if not filtered_cameras or not any(cam.is_connected for cam in filtered_cameras):
                        # No cameras for this interval - advance to next
                        pass
                    else:
                        # Filter to connected cameras only
                        connected_cameras = [cam for cam in filtered_cameras if cam.is_connected]

                        # Load fetch defaults from database
                        fetch_defaults = await self._load_fetch_defaults()

                        # Fire capture cycle as background task - don't block the interval loop
                        task = asyncio.create_task(
                            self._run_capture_cycle(
                                connected_cameras, camera_settings, timestamp, interval, fetch_defaults, capture_time
                            )
                        )
                        in_flight.add(task)

            except asyncio.CancelledError:
                logger.info("Interval task cancelled", extra={"interval": interval})
                break
            except Exception as e:
                logger.error("Error in interval capture", extra={"interval": interval, "error": str(e)})

            # Advance to next aligned timestamp
            next_aligned_ts = timestamp + interval

            # Sleep until next execution
            sleep_time = next_aligned_ts - time.time()
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            elif sleep_time < -interval / 2:
                # We're running significantly behind — jump to current alignment
                logger.warning(
                    "Interval running behind schedule",
                    extra={"interval": interval, "behind_seconds": round(-sleep_time, 1)},
                )
                # Re-sync to current time to avoid firing stale timestamps
                now_ts = int(time.time())
                elapsed = now_ts - self.common_start_timestamp
                next_aligned_ts = self.common_start_timestamp + ((elapsed // interval) + 1) * interval

    def _filter_cameras_for_interval(
        self, api_cameras: list[ApiCamera], camera_settings: dict[str, dict[str, Any]], interval: int
    ) -> list[ApiCamera]:
        """Filter API cameras based on database settings for this interval."""
        filtered = []
        for cam in api_cameras:
            settings = camera_settings.get(cam.id)
            if settings is None:
                # Camera not in database yet - skip until synced
                continue
            elif not settings.get("is_active", True):
                # Camera explicitly disabled
                continue
            else:
                # Check interval settings - only capture if explicitly enabled
                enabled_intervals = settings.get("enabled_intervals")
                if enabled_intervals and interval in enabled_intervals:
                    filtered.append(cam)
        return filtered

    async def _run_capture_cycle(
        self,
        cameras: list[ApiCamera],
        camera_settings: dict[str, dict[str, Any]],
        timestamp: int,
        interval: int,
        fetch_defaults: dict[str, Any],
        capture_time: datetime,
    ) -> None:
        """Run a capture cycle as a background task - handles capture, db recording, and logging."""
        try:
            use_distribution = self.camera_manager.should_use_camera_distribution(len(cameras))

            if use_distribution:
                results = await self._capture_distributed(cameras, camera_settings, timestamp, interval, fetch_defaults)
            else:
                results = await self._capture_concurrent(cameras, camera_settings, timestamp, interval, fetch_defaults)

            # Record captures to database
            await self._record_captures_to_db(results)

            # Log interval summary
            successful = sum(1 for r in results.values() if r.success)
            total = len(results)
            logger.debug(
                "Capture cycle complete",
                extra={
                    "interval": interval,
                    "successful": successful,
                    "total": total,
                    "time": capture_time.strftime("%H:%M:%S"),
                    "distributed": use_distribution,
                },
            )
        except Exception as e:
            logger.error("Capture cycle failed", extra={"interval": interval, "error": str(e)})

    async def _capture_distributed(
        self,
        cameras: list[ApiCamera],
        camera_settings: dict[str, dict[str, Any]],
        timestamp: int,
        interval: int,
        fetch_defaults: dict[str, Any],
    ) -> dict[str, CaptureResult]:
        """Capture cameras using hash-based distribution for rate limiting."""

        if not cameras:
            return {}

        # Calculate optimal offset based on camera count
        optimal_offset = self.camera_manager.calculate_optimal_offset_seconds(len(cameras))

        # Group cameras by consecutive offset (sorted by UUID for deterministic ordering)
        camera_offsets = calculate_consecutive_offsets(cameras, optimal_offset)
        camera_groups: dict[int, list[ApiCamera]] = defaultdict(list)
        for camera in cameras:
            offset = camera_offsets.get(camera.id, 0)
            camera_groups[offset].append(camera)

        # Log distribution at debug level
        slot_distribution = {f"{offset}s": len(cams) for offset, cams in sorted(camera_groups.items())}
        logger.debug(
            "Camera distribution",
            extra={"interval": interval, "camera_count": len(cameras), "slots": slot_distribution},
        )

        # Fire each group at its designated offset without blocking
        tasks: list[asyncio.Task] = []
        start_time = asyncio.get_event_loop().time()

        for offset, group_cameras in sorted(camera_groups.items()):
            # Wait until this group's offset time
            elapsed = asyncio.get_event_loop().time() - start_time
            wait_time = offset - elapsed
            if wait_time > 0:
                await asyncio.sleep(wait_time)

            # Fire and forget - create task without awaiting
            task = asyncio.create_task(
                self._capture_group(group_cameras, camera_settings, timestamp, interval, fetch_defaults)
            )
            tasks.append(task)

        # Wait for all captures to complete and gather results
        all_results: dict[str, CaptureResult] = {}
        for task in tasks:
            try:
                group_results = await task
                all_results.update(group_results)
            except Exception as e:
                logger.error("Capture group failed", extra={"error": str(e)})

        return all_results

    async def _capture_concurrent(
        self,
        cameras: list[ApiCamera],
        camera_settings: dict[str, dict[str, Any]],
        timestamp: int,
        interval: int,
        fetch_defaults: dict[str, Any],
    ) -> dict[str, CaptureResult]:
        """Capture all cameras concurrently (no distribution)."""
        return await self._capture_group(cameras, camera_settings, timestamp, interval, fetch_defaults)

    async def _capture_group(
        self,
        cameras: list[ApiCamera],
        camera_settings: dict[str, dict[str, Any]],
        timestamp: int,
        interval: int,
        fetch_defaults: dict[str, Any],
    ) -> dict[str, CaptureResult]:
        """Capture a group of cameras concurrently with rate limiting."""
        if not cameras:
            return {}

        concurrent_limit = self.camera_manager.calculate_effective_concurrent_limit()
        semaphore = asyncio.Semaphore(concurrent_limit)

        # Extract defaults from database settings
        default_capture_method = fetch_defaults.get("default_capture_method", "auto")
        default_rtsp_quality = fetch_defaults.get("default_rtsp_quality", "high")
        high_quality_snapshots = fetch_defaults.get("high_quality_snapshots", True)
        rtsp_output_format = fetch_defaults.get("rtsp_output_format", "png")
        png_compression_level = fetch_defaults.get("png_compression_level", 6)
        rtsp_capture_timeout = fetch_defaults.get("rtsp_capture_timeout", 15)
        max_retries = fetch_defaults.get("max_retries", 3)
        retry_delay = fetch_defaults.get("retry_delay", 2)

        async def capture_single_camera(camera: ApiCamera) -> tuple[str, CaptureResult]:
            """Capture a snapshot from one camera using its configured method and settings."""
            async with semaphore:
                # Get camera-specific settings
                settings = camera_settings.get(camera.id, {})
                capture_method = await self.camera_manager.get_effective_capture_method(
                    camera, settings, default_method=default_capture_method
                )
                rtsp_quality = settings.get("rtsp_quality") or default_rtsp_quality

                # Build output path - use appropriate extension based on capture method
                date_obj = datetime.fromtimestamp(timestamp)
                year = date_obj.strftime("%Y")
                month = date_obj.strftime("%m")
                day = date_obj.strftime("%d")

                output_dir = config.IMAGE_OUTPUT_PATH / camera.safe_name / f"{interval}s" / year / month / day
                # RTSP uses configured format (png or jpg), API always uses jpg
                file_ext = rtsp_output_format if capture_method == "rtsp" else "jpg"
                output_path = output_dir / f"{camera.safe_name}_{timestamp}.{file_ext}"

                # Capture based on method with retry logic
                if capture_method == "rtsp":
                    result = await self._capture_with_retry_rtsp(
                        camera,
                        str(output_path),
                        interval,
                        timestamp,
                        rtsp_quality,
                        rtsp_output_format,
                        png_compression_level,
                        rtsp_capture_timeout,
                        max_retries,
                        retry_delay,
                    )
                else:
                    result = await self._capture_with_retry_api(
                        camera,
                        str(output_path),
                        interval,
                        timestamp,
                        high_quality_snapshots,
                        max_retries,
                        retry_delay,
                    )

                return camera.name, result

        # Execute all captures concurrently
        tasks = [capture_single_camera(camera) for camera in cameras]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        capture_results: dict[str, CaptureResult] = {}
        for result in results:
            if isinstance(result, tuple):
                camera_name, capture_result = result
                capture_results[camera_name] = capture_result
            else:
                logger.error("Unexpected error in camera capture", extra={"error": str(result)})

        return capture_results

    async def _capture_with_retry_api(
        self,
        camera: ApiCamera,
        output_path: str,
        interval: int,
        timestamp: int,
        high_quality: bool,
        max_retries: int,
        retry_delay: int,
    ) -> CaptureResult:
        """Capture via API with retry logic using database settings."""
        result: CaptureResult | None = None

        for attempt in range(max_retries + 1):
            result = await self.camera_manager.capture_snapshot(
                camera, output_path, interval, timestamp, attempt, high_quality=high_quality
            )

            if result.success:
                return result

            if attempt < max_retries:
                logger.debug(
                    "Retrying API capture",
                    extra={
                        "camera": camera.name,
                        "interval": interval,
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                    },
                )
                await asyncio.sleep(retry_delay)

        # Return the last failed result
        return (
            result
            if result
            else CaptureResult(
                success=False,
                camera_id=camera.id,
                camera_safe_name=camera.safe_name,
                timestamp=timestamp,
                interval=interval,
                error_message="All retry attempts failed",
            )
        )

    async def _capture_with_retry_rtsp(
        self,
        camera: ApiCamera,
        output_path: str,
        interval: int,
        timestamp: int,
        rtsp_quality: str,
        rtsp_output_format: str,
        png_compression_level: int,
        rtsp_capture_timeout: int,
        max_retries: int,
        retry_delay: int,
    ) -> CaptureResult:
        """Capture via RTSP with retry logic using database settings."""
        result: CaptureResult | None = None

        for attempt in range(max_retries + 1):
            result = await self.camera_manager.capture_snapshot_rtsp(
                camera,
                output_path,
                interval,
                timestamp,
                quality=rtsp_quality,
                output_format=rtsp_output_format,
                png_compression_level=png_compression_level,
                capture_timeout=rtsp_capture_timeout,
            )

            if result.success:
                return result

            if attempt < max_retries:
                logger.debug(
                    "Retrying RTSP capture",
                    extra={
                        "camera": camera.name,
                        "interval": interval,
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                    },
                )
                await asyncio.sleep(retry_delay)

        # Return the last failed result
        return (
            result
            if result
            else CaptureResult(
                success=False,
                camera_id=camera.id,
                camera_safe_name=camera.safe_name,
                timestamp=timestamp,
                interval=interval,
                error_message="All retry attempts failed",
            )
        )

    async def _record_captures_to_db(self, results: dict[str, CaptureResult]) -> None:
        """Record capture results to the database and queue thumbnail generation."""
        if not results:
            return

        try:
            async with async_session() as session:
                for _camera_name, result in results.items():
                    # Store local datetime
                    capture_datetime = datetime.fromtimestamp(result.timestamp)

                    # Extract file name from path
                    file_name = Path(result.file_path).name if result.file_path else None

                    capture_data = CaptureCreate(
                        camera_id=result.camera_id,
                        camera_safe_name=result.camera_safe_name,
                        timestamp=result.timestamp,
                        capture_datetime=capture_datetime,
                        capture_date=capture_datetime.date(),
                        interval=result.interval,
                        status="success" if result.success else "failed",
                        capture_method=result.capture_method,
                        file_path=result.file_path,
                        file_name=file_name,
                        file_size=result.file_size,
                        error_message=result.error_message,
                        capture_duration_ms=result.capture_duration_ms,
                    )

                    await capture_crud.create(session, obj_in=capture_data)

                    # Log failed captures to activity feed
                    if not result.success:
                        await activity_crud.log_capture_failed(
                            session,
                            camera_id=result.camera_id,
                            camera_safe_name=result.camera_safe_name,
                            interval=result.interval,
                            error=result.error_message or "Unknown error",
                        )

                    # Queue thumbnail generation for successful captures
                    if result.success and result.file_path:
                        await image_service.queue_thumbnail(
                            result.file_path,
                            result.camera_safe_name,
                            result.timestamp,
                            result.interval,
                            datetime.fromtimestamp(result.timestamp).date(),
                        )

                await session.commit()
                successful = sum(1 for r in results.values() if r.success)
                logger.info(
                    "Recorded captures to database",
                    extra={"cameras": len(results), "successful": successful},
                )

        except Exception as e:
            logger.error("Failed to record captures to database", extra={"error": str(e)})
