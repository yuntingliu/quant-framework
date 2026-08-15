"""Six-stage Python component and project endpoints."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from alphalab.dataio import MissingDataError
from alphalab.pipeline.models import STAGE_ENTRYPOINTS
from alphalab.strategy.python_runtime import validate_python_source
from dashboard.backend.services import pipeline_service


StageName = Literal["universe", "selection", "timing", "portfolio", "risk", "execution"]
router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


class ComponentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component_id: str = Field(min_length=2, max_length=64)
    stage: StageName
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    source: str = Field(min_length=1, max_length=100_000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=500)


class ComponentVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=100_000)
    parameters: dict[str, Any] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    notes: str = Field(default="", max_length=500)


class CloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=2, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=100)


class SourceValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: StageName
    source: str = Field(min_length=1, max_length=100_000)


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str | None = Field(default=None, min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    components: dict[StageName, dict[str, Any]]
    settings: dict[str, Any] = Field(default_factory=dict)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: StageName
    profile: Literal["demo", "runtime"] = "demo"
    as_of_date: date | None = None


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="object not found")
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail="object already exists")
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, MissingDataError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/components")
def components(stage: StageName | None = None) -> list[dict[str, Any]]:
    return pipeline_service.list_components(stage)


@router.post("/components/validate")
def validate_component(request: SourceValidationRequest) -> dict[str, Any]:
    try:
        return validate_python_source(request.source, STAGE_ENTRYPOINTS[request.stage])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/components/{component_id}")
def component(component_id: str, version: int | None = None) -> dict[str, Any]:
    item = pipeline_service.get_component(component_id, version)
    if item is None:
        raise HTTPException(status_code=404, detail="component not found")
    return item


@router.post("/components", status_code=201)
def create_component(request: ComponentCreateRequest) -> dict[str, Any]:
    try:
        payload = request.model_dump()
        payload["component_id"] = payload.pop("component_id")
        return pipeline_service.create_component(payload)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/components/{component_id}/clone", status_code=201)
def clone_component(component_id: str, request: CloneRequest) -> dict[str, Any]:
    try:
        return pipeline_service.clone_component(component_id, request.target_id, request.name)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/components/{component_id}/versions", status_code=201)
def create_component_version(component_id: str, request: ComponentVersionRequest) -> dict[str, Any]:
    try:
        return pipeline_service.save_component_version(
            component_id, request.model_dump(exclude_none=True)
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.delete("/components/{component_id}", status_code=204)
def remove_component(component_id: str) -> Response:
    try:
        if not pipeline_service.delete_component(component_id):
            raise HTTPException(status_code=404, detail="component not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate_error(exc) from exc
    return Response(status_code=204)


@router.get("/projects")
def projects() -> list[dict[str, Any]]:
    return pipeline_service.list_projects()


@router.get("/projects/{project_id}")
def project(project_id: str) -> dict[str, Any]:
    item = pipeline_service.get_project(project_id)
    if item is None:
        raise HTTPException(status_code=404, detail="project not found")
    return item


@router.post("/projects", status_code=201)
def create_project(request: ProjectRequest) -> dict[str, Any]:
    if request.project_id is None:
        raise HTTPException(status_code=422, detail="project_id is required")
    try:
        payload = request.model_dump(exclude={"project_id"})
        payload["project_id"] = request.project_id
        return pipeline_service.create_project(payload)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.put("/projects/{project_id}")
def update_project(project_id: str, request: ProjectRequest) -> dict[str, Any]:
    if request.project_id is not None and request.project_id != project_id:
        raise HTTPException(status_code=422, detail="project_id cannot be changed")
    try:
        return pipeline_service.update_project(
            project_id, request.model_dump(exclude={"project_id"})
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/clone", status_code=201)
def clone_project(project_id: str, request: CloneRequest) -> dict[str, Any]:
    try:
        return pipeline_service.clone_project(project_id, request.target_id, request.name)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/preview")
def preview(project_id: str, request: PreviewRequest) -> dict[str, Any]:
    try:
        return pipeline_service.preview_project(
            project_id,
            stage=request.stage,
            profile=request.profile,
            as_of_date=request.as_of_date.isoformat() if request.as_of_date else None,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.delete("/projects/{project_id}", status_code=204)
def remove_project(project_id: str) -> Response:
    try:
        if not pipeline_service.delete_project(project_id):
            raise HTTPException(status_code=404, detail="project not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate_error(exc) from exc
    return Response(status_code=204)
