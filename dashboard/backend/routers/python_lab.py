"""Guarded Python Lab endpoints."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from alphalab.dataio import MissingDataError
from dashboard.backend.services import python_lab_service


router = APIRouter(prefix="/api/python-lab", tags=["python-lab"])


class PythonLabRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=2, max_length=64)
    profile: Literal["demo", "runtime"] = "demo"
    source: str = Field(min_length=1, max_length=100_000)
    as_of_date: date | None = None
    lookback_days: int = Field(default=120, ge=5, le=500)
    symbols: list[str] | None = Field(default=None, max_length=500)
    max_rows: int = Field(default=50_000, ge=100, le=50_000)
    confirm_python_execution: bool = False
    confirm_trusted_local: bool = False


class PythonLabPromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["component", "factor"]
    target_id: str | None = Field(default=None, min_length=1, max_length=80)
    apply_to_project: bool = False
    confirm_write: bool = False


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="object not found")
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=f"object already exists: {exc}")
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, MissingDataError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, TimeoutError):
        return HTTPException(status_code=408, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/capabilities")
def runtime_capabilities() -> dict:
    try:
        return python_lab_service.capabilities()
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/runs")
def runs(limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    return python_lab_service.list_runs(limit)


@router.get("/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    item = python_lab_service.get_run(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Python Lab run not found")
    return item


@router.post("/runs", status_code=201)
def execute(request: PythonLabRunRequest) -> dict:
    try:
        return python_lab_service.run(
            project_id=request.project_id,
            profile=request.profile,
            source=request.source,
            as_of_date=request.as_of_date.isoformat() if request.as_of_date else None,
            lookback_days=request.lookback_days,
            symbols=request.symbols,
            max_rows=request.max_rows,
            confirm_python_execution=request.confirm_python_execution,
            confirm_trusted_local=request.confirm_trusted_local,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/runs/{run_id}/promote")
def promote(run_id: str, request: PythonLabPromotionRequest) -> dict:
    try:
        return python_lab_service.promote(
            run_id,
            kind=request.kind,
            target_id=request.target_id,
            apply_to_project=request.apply_to_project,
            confirm_write=request.confirm_write,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
