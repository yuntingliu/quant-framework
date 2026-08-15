"""Paper-only order endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from alphalab.dataio import MissingDataError
from dashboard.backend.models import PaperOrderRequest, PaperRebalanceRequest
from dashboard.backend.services.result_service import (
    create_paper_order,
    execute_paper_rebalance,
    list_paper_fills,
    list_paper_orders,
    paper_account_summary,
    preview_paper_rebalance,
)

router = APIRouter(prefix="/api/paper", tags=["paper"])


@router.get("/orders")
def orders(limit: int = 100) -> list[dict]:
    return list_paper_orders(limit=limit)


@router.get("/account")
def account(account_id: str = "paper", profile: str = "demo") -> dict:
    try:
        return paper_account_summary(account_id, profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/fills")
def fills(account_id: str = "paper", limit: int = 100) -> list[dict]:
    return list_paper_fills(account_id, limit)


@router.post("/rebalance/preview")
def preview(request: PaperRebalanceRequest) -> dict:
    try:
        return preview_paper_rebalance(
            strategy_id=request.strategy_id,
            signal_id=request.signal_id,
            profile=request.profile,
            account_id=request.account_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="signal not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/rebalance/execute")
def execute(request: PaperRebalanceRequest) -> dict:
    try:
        return execute_paper_rebalance(
            strategy_id=request.strategy_id,
            signal_id=request.signal_id,
            profile=request.profile,
            account_id=request.account_id,
            confirm=request.confirm,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="signal not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/orders")
def submit_order(request: PaperOrderRequest) -> dict:
    try:
        return create_paper_order(
            request.symbol,
            request.action,
            request.quantity,
            request.price,
            request.signal_id,
            request.profile,
            request.account_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="symbol not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
