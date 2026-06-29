"""System endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from dashboard.backend.services.framework_service import store_stats

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/stats")
def stats() -> dict:
    return store_stats()


@router.get("/logs")
def logs() -> list[dict]:
    return [{"level": "info", "message": "Barebone workstation backend is running."}]

