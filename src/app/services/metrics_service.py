"""
Prometheus metrics service.

Generates Prometheus-format metrics for monitoring.
Focuses on application-specific metrics (cameras, captures, timelapses, jobs).
Host metrics (CPU, memory, disk) should be collected by dedicated tools like node_exporter.
"""

from datetime import datetime
from typing import Any

from crud import camera_crud, job_crud
from crud.capture_crud import capture_crud
from crud.timelapse_crud import timelapse_crud
from logging_config import get_logger

logger = get_logger(__name__)


class MetricsService:
    """Service for generating Prometheus-format metrics."""

    def __init__(self, start_time: datetime | None = None):
        self.start_time = start_time or datetime.now()

    def _format_metric(
        self,
        name: str,
        value: float | int,
        metric_type: str = "gauge",
        help_text: str = "",
        labels: dict[str, str] | None = None,
    ) -> str:
        """Format a single metric in Prometheus format."""
        lines = []

        # Add HELP and TYPE comments
        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")

        # Format labels if present
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{label_str}}} {value}")
        else:
            lines.append(f"{name} {value}")

        return "\n".join(lines)

    def _format_metric_with_labels(
        self,
        name: str,
        values: list[tuple[dict[str, str], float | int]],
        metric_type: str = "gauge",
        help_text: str = "",
    ) -> str:
        """Format a metric with multiple label sets."""
        lines = []

        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")

        for labels, value in values:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{label_str}}} {value}")

        return "\n".join(lines)

    async def get_service_metrics(self) -> str:
        """Get service uptime and status metrics."""
        metrics = []

        # Service uptime
        uptime_seconds = (datetime.now() - self.start_time).total_seconds()
        metrics.append(
            self._format_metric(
                "unifi_timelapse_uptime_seconds",
                uptime_seconds,
                "counter",
                "Service uptime in seconds",
            )
        )

        # Service enabled flags (all services always run, database controls runtime behavior)
        for service in ["fetch", "timelapse", "web"]:
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_service_enabled",
                    1,
                    "gauge",
                    f"Whether {service} service is enabled",
                    {"service": service},
                )
            )

        return "\n\n".join(metrics)

    async def get_camera_metrics(self, db: Any) -> str:
        """Get camera metrics."""
        metrics = []

        try:
            # Get all cameras (use get_multi with high limit to get all)
            cameras = await camera_crud.get_multi(db, limit=1000)
            total_cameras = len(cameras) if cameras else 0

            # Count by status
            active_cameras = sum(1 for c in cameras if c.is_active) if cameras else 0
            connected_cameras = sum(1 for c in cameras if c.is_connected) if cameras else 0
            recording_cameras = sum(1 for c in cameras if c.is_recording) if cameras else 0

            metrics.append(
                self._format_metric(
                    "unifi_timelapse_cameras_total",
                    total_cameras,
                    "gauge",
                    "Total number of cameras",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_cameras_active",
                    active_cameras,
                    "gauge",
                    "Number of active cameras",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_cameras_connected",
                    connected_cameras,
                    "gauge",
                    "Number of connected cameras",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_cameras_recording",
                    recording_cameras,
                    "gauge",
                    "Number of recording cameras",
                )
            )

        except Exception as e:
            logger.warning("Could not get camera metrics", extra={"error": str(e)})

        return "\n\n".join(metrics) if metrics else ""

    async def get_capture_metrics(self, db: Any) -> str:
        """Get capture statistics metrics."""
        metrics = []

        try:
            # Get capture stats in a single query
            stats = await capture_crud.get_stats(db)

            metrics.append(
                self._format_metric(
                    "unifi_timelapse_captures_total",
                    stats.total_captures,
                    "counter",
                    "Total number of captures",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_captures_success_total",
                    stats.successful_captures,
                    "counter",
                    "Total successful captures",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_captures_failed_total",
                    stats.failed_captures,
                    "counter",
                    "Total failed captures",
                )
            )

            # Success rate
            if stats.total_captures > 0:
                success_rate = (stats.successful_captures / stats.total_captures) * 100
                metrics.append(
                    self._format_metric(
                        "unifi_timelapse_capture_success_rate",
                        round(success_rate, 2),
                        "gauge",
                        "Capture success rate percentage",
                    )
                )

            # Total file size
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_captures_size_bytes_total",
                    stats.total_file_size,
                    "counter",
                    "Total size of all captured images in bytes",
                )
            )

        except Exception as e:
            logger.warning("Could not get capture metrics", extra={"error": str(e)})

        return "\n\n".join(metrics) if metrics else ""

    async def get_timelapse_metrics(self, db: Any) -> str:
        """Get timelapse statistics metrics."""
        metrics = []

        try:
            # Get timelapse stats
            stats = await timelapse_crud.get_stats(db)

            metrics.append(
                self._format_metric(
                    "unifi_timelapse_videos_total",
                    stats.total_timelapses,
                    "counter",
                    "Total number of timelapse videos",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_videos_completed",
                    stats.completed_timelapses,
                    "counter",
                    "Number of completed timelapse videos",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_videos_pending",
                    stats.pending_timelapses,
                    "gauge",
                    "Number of pending timelapse videos",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_videos_failed",
                    stats.failed_timelapses,
                    "counter",
                    "Number of failed timelapse videos",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_videos_duration_seconds_total",
                    stats.total_duration_seconds,
                    "counter",
                    "Total duration of all timelapse videos in seconds",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_videos_size_bytes_total",
                    stats.total_file_size,
                    "counter",
                    "Total size of all timelapse videos in bytes",
                )
            )

        except Exception as e:
            logger.warning("Could not get timelapse metrics", extra={"error": str(e)})

        return "\n\n".join(metrics) if metrics else ""

    async def get_job_metrics(self, db: Any) -> str:
        """Get job queue metrics."""
        metrics = []

        try:
            # Get job summary statistics
            summary = await job_crud.get_summary(db)

            metrics.append(
                self._format_metric(
                    "unifi_timelapse_jobs_active",
                    summary["active_jobs"],
                    "gauge",
                    "Number of active jobs (pending + running)",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_jobs_pending",
                    summary["pending_jobs"],
                    "gauge",
                    "Number of pending jobs",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_jobs_running",
                    summary["running_jobs"],
                    "gauge",
                    "Number of running jobs",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_jobs_completed_total",
                    summary["completed_jobs"],
                    "counter",
                    "Total completed jobs",
                )
            )
            metrics.append(
                self._format_metric(
                    "unifi_timelapse_jobs_failed_total",
                    summary["failed_jobs"],
                    "counter",
                    "Total failed jobs",
                )
            )

        except Exception as e:
            logger.warning("Could not get job metrics", extra={"error": str(e)})

        return "\n\n".join(metrics) if metrics else ""

    async def get_all_metrics(self, db: Any) -> str:
        """Get all metrics in Prometheus format."""
        sections = []

        # Add header comment
        sections.append("# LuxUPT Metrics")
        sections.append(f"# Generated at {datetime.now().isoformat()}")

        # Collect all application metrics
        service_metrics = await self.get_service_metrics()
        if service_metrics:
            sections.append(service_metrics)

        camera_metrics = await self.get_camera_metrics(db)
        if camera_metrics:
            sections.append(camera_metrics)

        capture_metrics = await self.get_capture_metrics(db)
        if capture_metrics:
            sections.append(capture_metrics)

        timelapse_metrics = await self.get_timelapse_metrics(db)
        if timelapse_metrics:
            sections.append(timelapse_metrics)

        job_metrics = await self.get_job_metrics(db)
        if job_metrics:
            sections.append(job_metrics)

        return "\n\n".join(sections) + "\n"
