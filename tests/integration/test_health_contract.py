"""Health probes return their declared response_model, and the status code follows the body.

Under ASGITransport the lifespan is skipped, so there is no camera manager: /health is at
best degraded (200) and the storage paths may be missing (503). Either way the body must
match the schema and 503 must mean ``status == "unhealthy"``.
"""

from httpx import AsyncClient

from app.models.enum_model import HealthStatus
from app.schemas.health_schema import HealthReport, LivenessReport, ReadinessReport


async def test_health_matches_contract(client: AsyncClient) -> None:
    resp = await client.get("/health")
    report = HealthReport.model_validate(resp.json())
    assert resp.status_code == (503 if report.status == HealthStatus.UNHEALTHY else 200)
    assert {"database", "camera_manager", "storage", "services"} <= report.checks.keys()
    # exclude_none keeps the per-check payload as it was: no null camera/services keys.
    assert "services" not in resp.json()["checks"]["database"]
    assert report.timestamp.endswith("+00:00")


async def test_liveness_matches_contract(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    report = LivenessReport.model_validate(resp.json())
    assert report.status == HealthStatus.HEALTHY


async def test_readiness_matches_contract(client: AsyncClient) -> None:
    resp = await client.get("/health/ready")
    report = ReadinessReport.model_validate(resp.json())
    assert resp.status_code == (503 if report.status == HealthStatus.UNHEALTHY else 200)
