"""Health probe contracts — the response shapes of /health, /health/live and /health/ready.

``timestamp`` is the aware-UTC instant already serialized with ``isoformat()`` (``…+00:00``),
so the wire format matches the datetime standard rather than pydantic's ``Z`` form.
"""

from pydantic import BaseModel

from app.models.enum_model import HealthStatus


class HealthCheckResult(BaseModel):
    """One subsystem check inside the full health report."""

    status: HealthStatus
    message: str
    # Camera manager check only.
    cameras_total: int | None = None
    cameras_connected: int | None = None
    # Services check only.
    services: dict[str, bool] | None = None


class HealthReport(BaseModel):
    """GET /health — overall status plus every subsystem check."""

    status: HealthStatus
    timestamp: str
    version: str
    # The build's short git SHA: `make smoke` proves the deployed stack runs THIS commit.
    commit: str
    uptime_seconds: int
    checks: dict[str, HealthCheckResult]


class LivenessReport(BaseModel):
    """GET /health/live — the process is up."""

    status: HealthStatus
    timestamp: str


class ReadinessReport(BaseModel):
    """GET /health/ready — the app can serve traffic."""

    status: HealthStatus
    timestamp: str
    message: str
