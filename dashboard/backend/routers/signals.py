"""Signal generation and persisted signal endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from alphalab.dataio import MissingDataError
from dashboard.backend.models import SignalRequest
from dashboard.backend.services.framework_service import generate_signal, latest_signal

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.post("/generate")
def generate(request: SignalRequest) -> dict:
    try:
        return generate_signal(
            request.strategy_id,
            request.as_of_date,
            request.persist,
            request.profile,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/latest")
def latest(strategy_id: str) -> dict:
    item = latest_signal(strategy_id)
    if item is None:
        raise HTTPException(status_code=404, detail="signal not found")
    return item
