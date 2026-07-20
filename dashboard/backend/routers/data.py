"""Data provider endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from alphalab.dataio import MissingDataError
from dashboard.backend.services.framework_service import (
    factor_returns,
    list_provider_status,
    load_manifest,
    market_bars,
    market_symbols,
)

router = APIRouter(prefix="/api/data", tags=["data"])


@router.get("/providers")
def providers() -> dict:
    try:
        return list_provider_status()
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/manifest")
def manifest() -> dict:
    try:
        return load_manifest()
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/market/symbols")
def symbols(profile: str = "demo") -> dict:
    try:
        return {"profile": profile, "symbols": market_symbols(profile)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/market/bars")
def bars(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    profile: str = "demo",
) -> dict:
    try:
        return {
            "symbol": symbol.upper(),
            "profile": profile,
            "rows": market_bars(symbol, start, end, profile),
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="symbol not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/factors/returns")
def factors(
    names: list[str] | None = Query(default=None),
    start: str | None = None,
    end: str | None = None,
    profile: str = "demo",
) -> dict:
    try:
        return factor_returns(names, start, end, profile)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"factor not found: {exc}") from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
