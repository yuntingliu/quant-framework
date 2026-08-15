"""Backtest endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from alphalab.dataio import MissingDataError
from dashboard.backend.services.backtest_analytics_service import (
    analyze_backtest,
    analyze_robustness,
    compare_backtests,
)
from dashboard.backend.services.result_service import (
    get_backtest,
    list_backtests,
)
from dashboard.backend.services.pipeline_service import run_project_backtest

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


class BacktestRequest(BaseModel):
    project_id: str
    start_date: str
    end_date: str
    profile: str = "demo"


class BacktestCompareRequest(BaseModel):
    ids: list[str] = Field(min_length=2, max_length=6)

    @field_validator("ids")
    @classmethod
    def unique_ids(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if len(cleaned) != len(values):
            raise ValueError("backtest ids must not be empty")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("backtest ids must be unique")
        return cleaned


@router.get("")
def backtests(limit: int = 20) -> list[dict]:
    return list_backtests(limit=limit)


@router.post("/compare")
def compare(request: BacktestCompareRequest) -> dict:
    try:
        return compare_backtests(request.ids)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"backtest not found: {exc.args[0]}",
        ) from exc


@router.get("/{backtest_id}")
def backtest_detail(backtest_id: str) -> dict:
    item = get_backtest(backtest_id)
    if item is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return item


@router.get("/{backtest_id}/analysis")
def backtest_analysis(backtest_id: str) -> dict:
    try:
        return analyze_backtest(backtest_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="backtest not found") from exc


@router.get("/{backtest_id}/robustness")
def backtest_robustness(backtest_id: str) -> dict:
    try:
        return analyze_robustness(backtest_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="backtest not found") from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/run")
def run_backtest_job(request: BacktestRequest) -> dict:
    try:
        return run_project_backtest(
            request.project_id,
            request.start_date,
            request.end_date,
            request.profile,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="strategy not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
