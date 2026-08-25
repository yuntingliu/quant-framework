"""Runtime data synchronization endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from alphalab.dataio.errors import DataLoadError, MissingDataError
from alphalab.dataio.sync import SyncRequest
from dashboard.backend.services import data_sync_service

router = APIRouter(prefix="/api/data-sync", tags=["data-sync"])


class ValidateRequest(BaseModel):
    dataset: str | None = None


class ConnectionTestRequest(BaseModel):
    template_id: str = "rq.a_share_daily"


@router.get("/health")
def health() -> dict:
    return data_sync_service.get_health()


@router.get("/catalog")
def catalog() -> dict:
    return data_sync_service.catalog()


@router.get("/templates")
def templates() -> dict:
    return data_sync_service.templates()


@router.post("/connection-test")
def connection_test(request: ConnectionTestRequest) -> dict:
    try:
        return data_sync_service.test_connection(request.template_id)
    except (ValueError, DataLoadError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/plan")
def plan(request: SyncRequest) -> dict:
    try:
        return data_sync_service.plan(request)
    except (ValueError, MissingDataError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs")
def jobs(limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    return data_sync_service.jobs(limit)


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
def submit(request: SyncRequest) -> dict:
    try:
        return data_sync_service.submit(request)
    except DataLoadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
def job(job_id: str) -> dict:
    item = data_sync_service.job(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="sync job not found")
    return item


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str) -> dict:
    item = data_sync_service.cancel(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="sync job not found")
    return item


@router.post("/validate")
def validate(request: ValidateRequest) -> dict | list[dict]:
    try:
        return data_sync_service.validate(request.dataset)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
