"""Deterministic research-pipeline endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from alphalab.dataio import DataLoadError
from dashboard.backend.services.research_service import get_research_manager

router = APIRouter(prefix="/api/research/runs", tags=["research"])


class ResearchRunRequest(BaseModel):
    strategy_id: str
    profile: str = "demo"
    start_date: str
    end_date: str
    account_id: str = "paper"

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"demo", "runtime"}:
            raise ValueError("profile must be demo or runtime")
        return normalized


@router.get("")
def list_runs(limit: int = 50) -> list[dict]:
    return get_research_manager().list(max(1, min(limit, 200)))


@router.post("", status_code=202)
def create_run(request: ResearchRunRequest) -> dict:
    try:
        return get_research_manager().submit(request.model_dump())
    except DataLoadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{run_id}")
def get_run(run_id: str) -> dict:
    item = get_research_manager().get(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="research run not found")
    return item


@router.post("/{run_id}/cancel")
def cancel_run(run_id: str) -> dict:
    item = get_research_manager().cancel(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="research run not found")
    return item


@router.post("/{run_id}/retry", status_code=202)
def retry_run(run_id: str) -> dict:
    try:
        return get_research_manager().retry(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="research run not found") from exc
    except (DataLoadError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
