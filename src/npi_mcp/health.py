"""Health endpoint for the HTTP transport."""

from __future__ import annotations

import time

from fastapi import APIRouter

from npi_mcp import __version__
from npi_mcp.models import HealthStatus

_STARTED_AT = time.monotonic()

router = APIRouter()


def current_health() -> HealthStatus:
    """Build the current health payload."""
    return HealthStatus(
        status="ok",
        version=__version__,
        uptime_seconds=round(time.monotonic() - _STARTED_AT, 3),
    )


@router.get("/health", response_model=HealthStatus, tags=["ops"])
async def health() -> HealthStatus:
    """Liveness probe: reports process status, package version, and uptime."""
    return current_health()
