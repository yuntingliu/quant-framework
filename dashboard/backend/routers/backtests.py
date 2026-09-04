"""Backtest endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from alphalab.dataio import MissingDataError
from dashboard.backend.services.backtest_analytics_service import (
    analyze_attribution,
    analyze_backtest,
    analyze_robustness,
    analyze_signal_diagnostics,
    compare_backtests,
)
from dashboard.backend.services.backtest_job_service import (
    get_backtest_job,
    list_backtest_jobs,
    submit_backtest_job,
)
from dashboard.backend.services.result_service import (
    get_backtest,
    get_backtest_event_page,
    get_backtest_summary,
    list_backtests,
)

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


class BacktestRequest(BaseModel):
    project_id: str = Field(min_length=1)
    start_date: str = Field(min_length=1)
    end_date: str = Field(min_length=1)
    profile: Literal["runtime"] = "runtime"
    revision: int | None = Field(default=None, ge=1)
    confirm_python_execution: bool


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


@router.post("/jobs", status_code=202)
def create_backtest_job(request: BacktestRequest) -> dict:
    if request.confirm_python_execution is not True:
        raise HTTPException(
            status_code=409,
            detail="backtest executes trusted local Python and requires confirmation",
        )
    try:
        return submit_backtest_job(request.model_dump(exclude={"confirm_python_execution"}))
    except KeyError:
        raise HTTPException(status_code=404, detail="strategy not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs")
def backtest_jobs(limit: int = 20) -> list[dict]:
    return list_backtest_jobs(limit=limit)


@router.get("/jobs/{job_id}")
def backtest_job(job_id: str) -> dict:
    item = get_backtest_job(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="backtest job not found")
    return item


@router.get("/{backtest_id}")
def backtest_detail(backtest_id: str) -> dict:
    item = get_backtest(backtest_id)
    if item is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return item


@router.get("/{backtest_id}/summary")
def backtest_summary(backtest_id: str) -> dict:
    item = get_backtest_summary(backtest_id)
    if item is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return item


@router.get("/{backtest_id}/events")
def backtest_events(
    backtest_id: str,
    kind: Literal["executions", "events"] = "events",
    offset: int = 0,
    limit: int = 50,
) -> dict:
    item = get_backtest_event_page(
        backtest_id,
        kind=kind,
        offset=max(0, offset),
        limit=max(1, min(limit, 100)),
    )
    if item is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return item


@router.get("/{backtest_id}/analysis")
def backtest_analysis(backtest_id: str) -> dict:
    try:
        return analyze_backtest(backtest_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="backtest not found") from exc


@router.get("/{backtest_id}/attribution")
def backtest_attribution(backtest_id: str) -> dict:
    try:
        return analyze_attribution(backtest_id)
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


@router.get("/{backtest_id}/signals")
def backtest_signals(backtest_id: str) -> dict:
    try:
        return analyze_signal_diagnostics(backtest_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="backtest not found") from exc
