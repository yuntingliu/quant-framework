"""Strategy template endpoints."""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alphalab.dataio import MissingDataError
from dashboard.backend.services.framework_service import (
    clone_strategy,
    delete_strategy,
    get_strategy_template,
    list_strategy_templates,
    preview_strategy_selection,
    research_rotation_strategy,
    research_timing_strategy,
    save_strategy,
    validate_strategy_config,
    validate_strategy_yaml,
)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


class StrategyValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yaml: str | None = Field(default=None, min_length=1, max_length=100_000)
    config: dict | None = None
    python_source: str | None = Field(default=None, max_length=100_000)

    @model_validator(mode="after")
    def exactly_one_representation(self) -> "StrategyValidationRequest":
        if (self.yaml is None) == (self.config is None):
            raise ValueError("provide exactly one of yaml or config")
        return self


class StrategyYamlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yaml: str = Field(min_length=1, max_length=100_000)
    python_source: str | None = Field(default=None, max_length=100_000)


class StrategySelectionPreviewRequest(StrategyValidationRequest):
    profile: Literal["demo", "runtime"] = "demo"
    as_of_date: date | None = None


class TimingResearchRequest(StrategyValidationRequest):
    profile: Literal["demo", "runtime"] = "demo"
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def ordered_dates(self) -> "TimingResearchRequest":
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        return self


class RotationResearchRequest(TimingResearchRequest):
    pass


class StrategyCloneRequest(BaseModel):
    target_id: str = Field(min_length=2, max_length=64)


@router.get("")
def strategies() -> list[dict]:
    return list_strategy_templates()


@router.get("/{strategy_id}")
def strategy(strategy_id: str) -> dict:
    try:
        item = get_strategy_template(strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return item


@router.post("/validate")
def validate_strategy(request: StrategyValidationRequest) -> dict:
    try:
        if request.yaml is not None:
            return validate_strategy_yaml(request.yaml, request.python_source)
        return validate_strategy_config(request.config or {}, request.python_source)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/selection-preview")
def selection_preview(request: StrategySelectionPreviewRequest) -> dict:
    try:
        return preview_strategy_selection(
            yaml_text=request.yaml,
            config=request.config,
            python_source=request.python_source,
            as_of_date=request.as_of_date.isoformat() if request.as_of_date else None,
            profile=request.profile,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/timing-research")
def timing_research(request: TimingResearchRequest) -> dict:
    try:
        return research_timing_strategy(
            yaml_text=request.yaml,
            config=request.config,
            python_source=request.python_source,
            start_date=request.start_date.isoformat(),
            end_date=request.end_date.isoformat(),
            profile=request.profile,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/rotation-research")
def rotation_research(request: RotationResearchRequest) -> dict:
    try:
        return research_rotation_strategy(
            yaml_text=request.yaml,
            config=request.config,
            python_source=request.python_source,
            start_date=request.start_date.isoformat(),
            end_date=request.end_date.isoformat(),
            profile=request.profile,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{strategy_id}/clone", status_code=201)
def clone(strategy_id: str, request: StrategyCloneRequest) -> dict:
    try:
        return clone_strategy(strategy_id, request.target_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy not found") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="strategy already exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{strategy_id}")
def update(strategy_id: str, request: StrategyYamlRequest) -> dict:
    try:
        return save_strategy(strategy_id, request.yaml, request.python_source)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{strategy_id}", status_code=204)
def remove(strategy_id: str) -> Response:
    try:
        deleted = delete_strategy(strategy_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="strategy not found")
    return Response(status_code=204)
