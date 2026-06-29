"""Data provider endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from dashboard.backend.services.framework_service import list_provider_status

router = APIRouter(prefix="/api/data", tags=["data"])


@router.get("/providers")
def providers() -> dict:
    return list_provider_status()

