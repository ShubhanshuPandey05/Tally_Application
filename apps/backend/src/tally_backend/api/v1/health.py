"""Health and observability endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from ..deps import HubDep, SessionDep, SettingsDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness. Must stay dependency-free.

    A liveness probe that checks the database restarts the whole fleet during a
    database blip, turning a partial outage into a total one. Readiness is the
    probe that is allowed to have opinions.
    """
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: SessionDep, settings: SettingsDep) -> dict:
    """Readiness: can this instance actually serve traffic?"""
    try:
        await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001 - report, do not raise
        database = f"error: {type(exc).__name__}"

    return {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "instance_id": settings.instance_id,
        "environment": settings.environment,
    }


@router.get("/fleet")
async def fleet(hub: HubDep) -> dict:
    """Connectors attached to *this* instance.

    Instance-local by design. With several replicas each answers for its own
    sockets; aggregating across the fleet is the monitoring system's job, not a
    fan-out call this endpoint should be making.
    """
    return hub.fleet_snapshot()
