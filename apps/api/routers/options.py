"""Bounded, local-data ETF option research and numerical pricing endpoints."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from alphalab.dataio import MissingDataError
from alphalab.dataio.option_data import load_option_snapshot
from alphalab.options import (
    EuropeanOption,
    analyze_chain,
    black_scholes,
    greeks,
    implied_volatility,
    monte_carlo,
    stress_grid,
)
from apps.api.services import options_service

router = APIRouter(prefix="/api/options", tags=["options"])


class PricingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    maturity: float = Field(ge=0)
    rate: float
    volatility: float = Field(ge=0)
    dividend_yield: float
    option_type: Literal["C", "P"]
    market_price: float | None = Field(default=None, ge=0)
    include_mc: bool = False
    paths: int = Field(default=100000, ge=2, le=500000)
    seed: int = Field(default=42, ge=0)

    def option(self):
        return EuropeanOption(
            self.spot,
            self.strike,
            self.maturity,
            self.rate,
            self.volatility,
            self.option_type,
            self.dividend_yield,
        )


class StressRequest(PricingRequest):
    multiplier: float = Field(gt=0)
    quantity: float = 1
    days_forward: float = Field(default=0, ge=0)
    jump_intensity: float = Field(default=1, ge=0, le=100)
    jump_mean: float = Field(default=-0.15, ge=-5, le=2)
    jump_volatility: float = Field(default=0.15, ge=0, le=2)


class OptionSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    underlyings: list[str] = Field(min_length=1, max_length=20)
    start: date
    end: date
    overlap: int = Field(default=3, ge=0, le=60)
    confirm_python_execution: bool = False


class PortfolioLegRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    underlying: str = Field(max_length=32)
    contract: str = Field(min_length=1, max_length=40)
    quantity: float = Field(ge=-100000, le=100000)


class PortfolioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    day: date
    legs: list[PortfolioLegRequest] = Field(min_length=1, max_length=20)
    rate: float | None = None
    days_forward: float = Field(default=0, ge=0, le=365)
    spot_shocks: list[float] = Field(
        default_factory=lambda: [-0.4, -0.3, -0.2, -0.1, -0.05, 0, 0.05, 0.1, 0.2, 0.3, 0.4],
        min_length=1,
        max_length=21,
    )
    volatility_shifts: list[float] = Field(
        default_factory=lambda: [0, 0.1, 0.3], min_length=1, max_length=9
    )
    include_mc: bool = True
    paths: int = Field(default=100000, ge=1000, le=250000)
    seed: int = Field(default=42, ge=0)
    correlation: float = Field(default=0.8, ge=0, le=1)
    jump_intensity: float = Field(default=1, ge=0, le=100)
    jump_mean: float = Field(default=-0.15, ge=-5, le=2)
    jump_volatility: float = Field(default=0.15, ge=0, le=2)


@router.get("/universe")
def universe() -> dict:
    return options_service.universe()


@router.get("/analysis")
def analysis(
    underlying: str,
    day: date,
    rate: float | None = None,
    price_field: Literal["close", "settlement"] = "close",
) -> dict:
    try:
        return options_service.analyze_snapshot(underlying, day, rate=rate, price_field=price_field)
    except MissingDataError as exc:
        raise HTTPException(404, str(exc)) from None
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from None
    except (OSError, KeyError):
        raise HTTPException(
            503, "Option analysis data is unavailable; validate the local datasets"
        ) from None


@router.get("/history")
def history(
    underlying: str, end: date | None = None, limit: int = Query(default=40, ge=1, le=90)
) -> dict:
    try:
        return options_service.history(underlying, end=end, limit=limit)
    except (MissingDataError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/quality")
def quality() -> dict:
    return options_service.quality()


def _sync_source(request):
    from alphalab.dataio.option_snapshots import validate_option_dates
    from alphalab.dataio.recipes import render_builtin_recipe

    if not request.confirm_python_execution:
        raise HTTPException(422, "Executing the visible built-in data recipe requires confirmation")
    validate_option_dates(request.start, request.end)
    from alphalab.dataio.symbols import to_rq_symbol

    symbols = list(dict.fromkeys(to_rq_symbol(v) for v in request.underlyings))
    if not set(symbols).issubset(options_service.ETF_NAMES):
        raise ValueError("Select a supported ETF option underlying")
    return render_builtin_recipe(
        "rq.etf_options", start=str(request.start), end=str(request.end), symbols=symbols
    ).replace("OVERLAP_SESSIONS = 3", f"OVERLAP_SESSIONS = {request.overlap}")


@router.post("/sync/plan")
def sync_plan(request: OptionSyncRequest) -> dict:
    from alphalab.dataio.recipes import execute_data_recipe

    try:
        source = _sync_source(request)
        return {"source": source, **execute_data_recipe(source, mode="plan")}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/sync/jobs", status_code=202)
def sync_job(request: OptionSyncRequest) -> dict:
    from apps.api.services.data_sync_service import get_job_manager

    try:
        source = _sync_source(request)
        job = get_job_manager().submit_recipe(source, project_id="etf-options-workbench")
        return {key: job.get(key) for key in ("id", "status", "message", "progress", "total")}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except RuntimeError:
        raise HTTPException(
            409, "A synchronization job is already active; wait or cancel it first"
        ) from None


@router.post("/portfolio")
def portfolio(request: PortfolioRequest) -> dict:
    try:
        return options_service.portfolio(request.model_dump(mode="json"))
    except MissingDataError as exc:
        raise HTTPException(404, str(exc)) from None
    except (ValueError, OverflowError, FloatingPointError) as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/chain")
def chain(
    underlying: str,
    day: date,
    rate: float,
    dividend_yield: float | None = None,
    price_field: Literal["close", "settlement"] = "close",
    carry_mode: Literal["input_yield", "put_call_forward"] = "input_yield",
) -> dict:
    try:
        frame = load_option_snapshot(underlying, day)
        return analyze_chain(
            frame,
            rate=rate,
            dividend_yield=dividend_yield,
            price_field=price_field,
            carry_mode=carry_mode,
        )
    except MissingDataError as exc:
        raise HTTPException(404, str(exc)) from None
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(422, "Invalid option data or pricing inputs") from None
    except (OSError, KeyError):
        raise HTTPException(503, "Option snapshot is unavailable; validate or resync it") from None


@router.post("/price")
def price(request: PricingRequest) -> dict:
    try:
        option = request.option()
        result = {
            "bs_price": black_scholes(option),
            "unit": "per_underlying_unit",
            "greeks": greeks(option) if option.maturity > 0 and option.volatility > 0 else None,
        }
        if request.market_price is not None:
            result["iv"] = implied_volatility(request.market_price, option)
        if request.include_mc:
            result["mc"] = monte_carlo(option, paths=request.paths, seed=request.seed)
        return result
    except (ValueError, OverflowError, FloatingPointError):
        raise HTTPException(422, "Invalid European option price or numerical inputs") from None


@router.post("/stress")
def stress(request: StressRequest) -> dict:
    try:
        return stress_grid(
            request.option(),
            multiplier=request.multiplier,
            quantity=request.quantity,
            days_forward=request.days_forward,
            paths=request.paths,
            seed=request.seed,
            jump_intensity=request.jump_intensity,
            jump_mean=request.jump_mean,
            jump_volatility=request.jump_volatility,
        )
    except (ValueError, OverflowError, FloatingPointError):
        raise HTTPException(422, "Invalid stress scenario or numerical inputs") from None
