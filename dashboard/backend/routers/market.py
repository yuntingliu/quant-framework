"""Factor-based market analytics endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from alphalab.dataio import MissingDataError
from dashboard.backend.services import market_analytics_service

router = APIRouter(prefix="/api/market", tags=["market"])


class CustomRiskFactorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["runtime"] = "runtime"
    name: str = Field(min_length=1, max_length=80)
    expression: str = Field(min_length=1, max_length=500)
    start_date: str | None = None
    end_date: str | None = None


def _factor_names(factors: str | None) -> list[str] | None:
    return factors.split(",") if factors else None


def _call(function, *args, **kwargs) -> dict:
    try:
        return function(*args, **kwargs)
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc).strip("'")) from exc


@router.get("/kpi")
def kpi(
    profile: Literal["runtime"] = "runtime",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    return _call(market_analytics_service.compute_kpi, profile, start, end)


@router.get("/cumulative-returns")
def cumulative_returns(
    profile: Literal["runtime"] = "runtime",
    start: str | None = None,
    end: str | None = None,
    factors: str | None = None,
) -> dict:
    return _call(
        market_analytics_service.compute_cumulative_returns,
        profile,
        start,
        end,
        _factor_names(factors),
    )


@router.get("/factor-stats")
def factor_stats(
    profile: Literal["runtime"] = "runtime",
    start: str | None = None,
    end: str | None = None,
    factors: str | None = None,
) -> dict:
    return _call(
        market_analytics_service.compute_factor_stats,
        profile,
        start,
        end,
        _factor_names(factors),
    )


@router.get("/drawdowns")
def drawdowns(
    profile: Literal["runtime"] = "runtime",
    start: str | None = None,
    end: str | None = None,
    top_n: int = Query(5, ge=1, le=20),
) -> dict:
    return _call(
        market_analytics_service.compute_drawdowns,
        profile,
        start,
        end,
        top_n,
    )


@router.get("/annual-returns")
def annual_returns(
    profile: Literal["runtime"] = "runtime",
    start: str | None = None,
    end: str | None = None,
    factors: str | None = None,
) -> dict:
    return _call(
        market_analytics_service.compute_annual_returns,
        profile,
        start,
        end,
        _factor_names(factors),
    )


@router.get("/volatility")
def volatility(
    profile: Literal["runtime"] = "runtime",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    return _call(market_analytics_service.compute_volatility, profile, start, end)


@router.get("/correlation")
def correlation(
    profile: Literal["runtime"] = "runtime",
    start: str | None = None,
    end: str | None = None,
    factors: str | None = None,
) -> dict:
    return _call(
        market_analytics_service.compute_correlation,
        profile,
        start,
        end,
        _factor_names(factors),
    )


@router.post("/custom-risk-factor/evaluate")
def custom_risk_factor(request: CustomRiskFactorRequest) -> dict:
    return _call(
        market_analytics_service.compute_custom_risk_factor,
        request.name,
        request.expression,
        request.profile,
        request.start_date,
        request.end_date,
    )
