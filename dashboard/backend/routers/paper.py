"""Paper-only order endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from alphalab.dataio import MissingDataError
from dashboard.backend.models import PaperOrderRequest
from dashboard.backend.services.framework_service import create_paper_order, list_paper_orders

router = APIRouter(prefix="/api/paper", tags=["paper"])


@router.get("/orders")
def orders(limit: int = 100) -> list[dict]:
    return list_paper_orders(limit=limit)


@router.post("/orders")
def submit_order(request: PaperOrderRequest) -> dict:
    try:
        return create_paper_order(
            request.symbol,
            request.action,
            request.quantity,
            request.price,
            request.signal_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="symbol not found") from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
