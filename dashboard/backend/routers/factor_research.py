"""Professional factor-definition and cross-sectional evaluation endpoints."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from alphalab.analytics import evaluate_factor
from alphalab.dataio import MissingDataError
from alphalab.factors import list_factors
from alphalab.strategy import FactorSpec, UniverseSpec
from dashboard.backend.services.data_service import _engine

router = APIRouter(prefix="/api/factor-research", tags=["factor-research"])


class FactorEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["demo", "runtime"] = "demo"
    name: str = Field(min_length=1, max_length=80)
    source: Literal["technical", "fundamental", "expression"] = "technical"
    expression: str | None = Field(default=None, max_length=500)
    direction: Literal["long", "short"] = "long"
    winsorize: float = Field(default=0.01, ge=0, lt=0.25)
    neutralize: list[Literal["market_cap"]] = Field(default_factory=list)
    start_date: str
    end_date: str
    frequency: Literal["monthly", "weekly"] = "monthly"
    quantiles: int = Field(default=5, ge=3, le=10)
    symbols: list[str] | None = Field(default=None, max_length=2_000)
    min_price: float = Field(default=0.0, ge=0)
    min_history_days: int = Field(default=60, ge=2, le=2_000)
    min_average_amount: float = Field(default=0.0, ge=0)


@router.get("/library")
def factor_library() -> dict:
    rows = [
        {
            "name": factor.name,
            "source": factor.kind,
            "description": factor.description,
        }
        for factor in list_factors()
    ]
    return {
        "factors": rows,
        "expression_functions": ["abs", "clip", "log", "rank", "sqrt", "zscore"],
        "neutralizers": ["market_cap"],
    }


@router.post("/evaluate")
def evaluate(request: FactorEvaluationRequest) -> dict:
    try:
        factor = FactorSpec(
            name=request.name,
            weight=1.0,
            source=request.source,
            expression=request.expression,
            direction=request.direction,
            winsorize=request.winsorize,
            neutralize=tuple(request.neutralize),
        )
        universe = UniverseSpec(
            symbols=tuple(request.symbols or ()),
            min_price=request.min_price,
            min_history_days=request.min_history_days,
            min_average_amount=request.min_average_amount,
        )
        return evaluate_factor(
            _engine(request.profile),
            factor,
            request.start_date,
            request.end_date,
            universe=universe,
            frequency=request.frequency,
            quantiles=request.quantiles,
        )
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["router"]
