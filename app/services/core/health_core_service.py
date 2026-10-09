"""
Health check service.

Provides comprehensive health checks for the application.
"""

import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.camera_manager import CameraManager
from app.core import config
from app.core.logging_config import get_logger
from app.db import maintenance as db_maintenance
from app.models.enum_model import HealthStatus
from app.schemas.health_schema import (
    HealthCheckResult,
    HealthReport,
    LivenessReport,
    ReadinessReport,
)

logger = get_logger(__name__)


class HealthCoreService:
    """Service for performing health checks."""

    def __init__(self, camera_manager: CameraManager | None = None):
        self.camera_manager = camera_manager
        self.start_time = datetime.now(UTC)

    async def check_database(self, db: AsyncSession) -> dict[str, Any]:
        """Check database connectivity."""
        try:
            # Simple query to verify database is accessible
            await db_maintenance.ping(db)

            return {
                "status": HealthStatus.HEALTHY,
                "message": "Database connected",
            }
        except Exception as e:
            logger.exception("Database health check failed", extra={"error": str(e)})
            return {
                "status": HealthStatus.UNHEALTHY,
                "message": f"Database error: {str(e)}",
            }

    async def check_camera_manager(self) -> dict[str, Any]:
        """Check camera manager status."""
        if not self.camera_manager:
            return {
                "status": HealthStatus.DEGRADED,
                "message": "Camera manager not initialized",
            }

        try:
            # Check if we have any cameras discovered
            cameras = await self.camera_manager.get_cameras()
            camera_count = len(cameras) if cameras else 0
            # post-db-filter: `cameras` is the UniFi Protect API response, not a query —
            # there is no WHERE to push this into.
            connected_count = (
                # post-db-filter: the Protect API response, not a query — no WHERE to use
                sum(1 for c in cameras if c.is_connected) if cameras else 0
            )

            if camera_count == 0:
                return {
                    "status": HealthStatus.DEGRADED,
                    "message": "No cameras discovered",
                    "cameras_total": 0,
                    "cameras_connected": 0,
                }

            if connected_count == 0:
                return {
                    "status": HealthStatus.DEGRADED,
                    "message": "No cameras connected",
                    "cameras_total": camera_count,
                    "cameras_connected": 0,
                }

            return {
                "status": HealthStatus.HEALTHY,
                "message": "Camera manager operational",
                "cameras_total": camera_count,
                "cameras_connected": connected_count,
            }
        except Exception as e:
            logger.exception(
                "Camera manager health check failed", extra={"error": str(e)}
            )
            return {
                "status": HealthStatus.UNHEALTHY,
                "message": f"Camera manager error: {str(e)}",
            }

    def check_storage(self) -> dict[str, Any]:
        """Check storage availability."""
        issues = []

        for path_name, path in [
            ("images", config.IMAGE_OUTPUT_PATH),
            ("videos", config.VIDEO_OUTPUT_PATH),
        ]:
            if not path.exists():
                issues.append(f"{path_name} path does not exist")
                continue

            if not os.access(path, os.W_OK):
                issues.append(f"{path_name} path is not writable")

        if issues:
            return {
                "status": HealthStatus.UNHEALTHY,
                "message": "; ".join(issues),
            }

        return {
            "status": HealthStatus.HEALTHY,
            "message": "Storage accessible",
        }

    def check_services_enabled(self) -> dict[str, Any]:
        """Check which services are enabled."""
        # All services always run (database controls runtime behavior)
        services = {
            "fetch": True,
            "timelapse": True,
            "web": True,
        }

        return {
            "status": HealthStatus.HEALTHY,
            "message": "Services enabled: fetch, timelapse, web",
            "services": services,
        }

    async def get_health_status(self, db: AsyncSession) -> HealthReport:
        """Get comprehensive health status."""
        checks = {}
        overall_status = HealthStatus.HEALTHY

        # Database check
        checks["database"] = await self.check_database(db)
        if checks["database"]["status"] == HealthStatus.UNHEALTHY:
            overall_status = HealthStatus.UNHEALTHY
        elif (
            checks["database"]["status"] == HealthStatus.DEGRADED
            and overall_status == HealthStatus.HEALTHY
        ):
            overall_status = HealthStatus.DEGRADED

        # Camera manager check
        checks["camera_manager"] = await self.check_camera_manager()
        if checks["camera_manager"]["status"] == HealthStatus.UNHEALTHY:
            overall_status = HealthStatus.UNHEALTHY
        elif (
            checks["camera_manager"]["status"] == HealthStatus.DEGRADED
            and overall_status == HealthStatus.HEALTHY
        ):
            overall_status = HealthStatus.DEGRADED

        # Storage check
        checks["storage"] = self.check_storage()
        if checks["storage"]["status"] == HealthStatus.UNHEALTHY:
            overall_status = HealthStatus.UNHEALTHY
        elif (
            checks["storage"]["status"] == HealthStatus.DEGRADED
            and overall_status == HealthStatus.HEALTHY
        ):
            overall_status = HealthStatus.DEGRADED

        # Services check
        checks["services"] = self.check_services_enabled()
        if (
            checks["services"]["status"] == HealthStatus.DEGRADED
            and overall_status == HealthStatus.HEALTHY
        ):
            overall_status = HealthStatus.DEGRADED

        # Calculate uptime
        uptime_seconds = (datetime.now(UTC) - self.start_time).total_seconds()

        return HealthReport(
            status=overall_status,
            timestamp=datetime.now(UTC).isoformat(),
            version=os.getenv("BUILD_VERSION", "dev"),
            uptime_seconds=int(uptime_seconds),
            checks={
                name: HealthCheckResult.model_validate(check)
                for name, check in checks.items()
            },
        )

    async def get_liveness(self) -> LivenessReport:
        """Simple liveness check (is the application running)."""
        return LivenessReport(
            status=HealthStatus.HEALTHY,
            timestamp=datetime.now(UTC).isoformat(),
        )

    async def get_readiness(self, db: AsyncSession) -> ReadinessReport:
        """Readiness check (is the application ready to serve traffic)."""
        # Check database connectivity
        db_check = await self.check_database(db)

        if db_check["status"] == HealthStatus.UNHEALTHY:
            return ReadinessReport(
                status=HealthStatus.UNHEALTHY,
                timestamp=datetime.now(UTC).isoformat(),
                message="Database not available",
            )

        # Check camera manager
        camera_check = await self.check_camera_manager()

        # Camera manager being degraded (no cameras) shouldn't block readiness
        # but being unhealthy (error) should
        if camera_check["status"] == HealthStatus.UNHEALTHY:
            return ReadinessReport(
                status=HealthStatus.UNHEALTHY,
                timestamp=datetime.now(UTC).isoformat(),
                message="Camera manager error",
            )

        return ReadinessReport(
            status=HealthStatus.HEALTHY,
            timestamp=datetime.now(UTC).isoformat(),
            message="Application ready",
        )
