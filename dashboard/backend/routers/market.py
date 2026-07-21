"""Factor-based market analytics endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from alphalab.dataio import MissingDataError
from dashboard.backend.services import market_analytics_service

router = APIRouter(prefix="/api/market", tags=["market"])


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
    profile: str = "demo",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    return _call(market_analytics_service.compute_kpi, profile, start, end)


@router.get("/cumulative-returns")
def cumulative_returns(
    profile: str = "demo",
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
    profile: str = "demo",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    return _call(market_analytics_service.compute_factor_stats, profile, start, end)


@router.get("/drawdowns")
def drawdowns(
    profile: str = "demo",
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
    profile: str = "demo",
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
    profile: str = "demo",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    return _call(market_analytics_service.compute_volatility, profile, start, end)


@router.get("/correlation")
def correlation(
    profile: str = "demo",
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
