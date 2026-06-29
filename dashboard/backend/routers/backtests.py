"""Backtest endpoints."""
from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from dashboard.backend.services.framework_service import list_backtests, run_strategy_backtest

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


class BacktestRequest(BaseModel):
    strategy_id: str
    start_date: str
    end_date: str


@router.get("")
def backtests(limit: int = 20) -> list[dict]:
    return list_backtests(limit=limit)


@router.post("/run")
def run_backtest_job(request: BacktestRequest) -> dict:
    try:
        return run_strategy_backtest(request.strategy_id, request.start_date, request.end_date)
    except KeyError:
        raise HTTPException(status_code=404, detail="strategy not found") from None

