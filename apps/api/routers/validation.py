"""Project validation.py authoring API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from alphalab.validation.source import ValidationSourceError
from apps.api.services import validation_service

router = APIRouter(prefix="/api/validation", tags=["validation-sdk-v1"])


class ValidationSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=300_000)
    expected_source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    confirm_write: bool


class ValidationParameterEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entrypoint_id: str = Field(min_length=1, max_length=100)
    parameter: str = Field(min_length=1, max_length=100)
    value: Any


class ValidationParameterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[ValidationParameterEdit] = Field(min_length=1, max_length=100)
    expected_source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    confirm_write: bool


class ValidationCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=300_000)


class ValidationMigrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    confirm_write: bool


def _raise_error(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail="strategy project not found") from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, RuntimeError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}")
def validation_workspace(project_id: str) -> dict:
    try:
        return validation_service.get_workspace(project_id)
    except (KeyError, ValueError, ValidationSourceError) as exc:
        _raise_error(exc)


@router.put("/projects/{project_id}")
def save_validation_source(project_id: str, request: ValidationSourceRequest) -> dict:
    if request.confirm_write is not True:
        raise HTTPException(status_code=409, detail="validation source write requires confirmation")
    try:
        return validation_service.update_source(
            project_id,
            request.source,
            expected_source_sha256=request.expected_source_sha256,
        )
    except (KeyError, PermissionError, RuntimeError, ValueError, ValidationSourceError) as exc:
        _raise_error(exc)


@router.post("/projects/{project_id}/parameters")
def save_validation_parameters(project_id: str, request: ValidationParameterRequest) -> dict:
    if request.confirm_write is not True:
        raise HTTPException(status_code=409, detail="validation parameter write requires confirmation")
    try:
        return validation_service.update_parameters(
            project_id,
            [item.model_dump() for item in request.edits],
            expected_source_sha256=request.expected_source_sha256,
        )
    except (KeyError, PermissionError, RuntimeError, ValueError, ValidationSourceError) as exc:
        _raise_error(exc)


@router.post("/projects/{project_id}/migrate-default")
def migrate_validation_source(
    project_id: str, request: ValidationMigrationRequest
) -> dict:
    if request.confirm_write is not True:
        raise HTTPException(status_code=409, detail="validation migration requires confirmation")
    try:
        return validation_service.migrate_to_default(
            project_id,
            expected_source_sha256=request.expected_source_sha256,
        )
    except (KeyError, PermissionError, RuntimeError, ValueError, ValidationSourceError) as exc:
        _raise_error(exc)


@router.post("/validate")
def validate_validation_source(request: ValidationCheckRequest) -> dict:
    try:
        return validation_service.validate_source(request.source)
    except (ValueError, ValidationSourceError) as exc:
        _raise_error(exc)
