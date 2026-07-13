"""Signal generation and persisted signal endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from dashboard.backend.models import SignalRequest
from dashboard.backend.services.framework_service import generate_signal, latest_signal

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.post("/generate")
def generate(request: SignalRequest) -> dict:
    try:
        return generate_signal(request.strategy_id, request.as_of_date, request.persist)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy not found") from exc


@router.get("/latest")
def latest(strategy_id: str) -> dict:
    item = latest_signal(strategy_id)
    if item is None:
        raise HTTPException(status_code=404, detail="signal not found")
    return item
