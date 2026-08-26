"""Runtime data synchronization endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from alphalab.dataio.errors import DataLoadError, MissingDataError
from alphalab.dataio.recipes import DataRecipeError
from alphalab.dataio.sync import SyncRequest
from dashboard.backend.services import data_sync_service

router = APIRouter(prefix="/api/data-sync", tags=["data-sync"])


class ValidateRequest(BaseModel):
    dataset: str | None = None


class ConnectionTestRequest(BaseModel):
    template_id: str = "rq.a_share_daily"


class RecipeSourceRequest(BaseModel):
    source: str = Field(min_length=1, max_length=300_000)
    expected_source_sha256: str | None = None
    confirm_write: bool = False


class RecipeTemplateRequest(BaseModel):
    expected_source_sha256: str | None = None
    confirm_write: bool = False


class RecipeParametersRequest(BaseModel):
    start: str
    end: str
    symbols: list[str] | None = None
    expected_source_sha256: str | None = None
    confirm_write: bool = False


class CustomRecipeTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    expected_source_sha256: str | None = None
    confirm_save: bool = False


class RecipeExecutionRequest(BaseModel):
    confirm_python_execution: bool = False


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


@router.delete("/recipe-templates/{template_id}")
def delete_recipe_template(
    template_id: str,
    confirm_delete: bool = Query(default=False),
) -> dict:
    _confirmed(confirm_delete, "Deleting a custom data recipe template requires confirmation")
    try:
        deleted = data_sync_service.delete_custom_recipe_template(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="data recipe template not found")
    return {"deleted": True, "id": template_id}


@router.get("/recipes/{project_id}")
def recipe_workspace(project_id: str) -> dict:
    try:
        return data_sync_service.recipe_workspace(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/recipes/{project_id}")
def save_recipe(project_id: str, request: RecipeSourceRequest) -> dict:
    _confirmed(request.confirm_write, "Saving Python data recipe source requires confirmation")
    try:
        return data_sync_service.save_recipe_source(
            project_id,
            request.source,
            expected_source_sha256=request.expected_source_sha256,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, DataRecipeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/recipes/{project_id}/templates/{template_id}")
def apply_recipe_template(
    project_id: str,
    template_id: str,
    request: RecipeTemplateRequest,
) -> dict:
    _confirmed(request.confirm_write, "Replacing Python data recipe source requires confirmation")
    try:
        return data_sync_service.apply_recipe_template(
            project_id,
            template_id,
            expected_source_sha256=request.expected_source_sha256,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="data recipe template not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, DataRecipeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/recipes/{project_id}/parameters")
def update_recipe_parameters(
    project_id: str,
    request: RecipeParametersRequest,
) -> dict:
    _confirmed(request.confirm_write, "Writing parameters into Python source requires confirmation")
    try:
        return data_sync_service.update_recipe_parameters(
            project_id,
            start=request.start,
            end=request.end,
            symbols=request.symbols,
            expected_source_sha256=request.expected_source_sha256,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="data recipe draft not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, DataRecipeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/recipes/{project_id}/templates")
def save_custom_recipe_template(
    project_id: str,
    request: CustomRecipeTemplateRequest,
) -> dict:
    _confirmed(request.confirm_save, "Saving a custom data recipe template requires confirmation")
    try:
        return data_sync_service.save_custom_recipe_template(
            project_id,
            name=request.name,
            description=request.description,
            expected_source_sha256=request.expected_source_sha256,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="data recipe draft not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, DataRecipeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/recipes/{project_id}/plan")
def plan_recipe(project_id: str, request: RecipeExecutionRequest) -> dict:
    _confirmed(
        request.confirm_python_execution, "Executing Python data recipe requires confirmation"
    )
    try:
        return data_sync_service.plan_recipe(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="data recipe draft not found") from exc
    except (ValueError, DataRecipeError, MissingDataError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/recipes/{project_id}/jobs", status_code=status.HTTP_202_ACCEPTED)
def submit_recipe(project_id: str, request: RecipeExecutionRequest) -> dict:
    _confirmed(
        request.confirm_python_execution, "Executing Python data recipe requires confirmation"
    )
    try:
        return data_sync_service.submit_recipe(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="data recipe draft not found") from exc
    except DataLoadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, DataRecipeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


def _confirmed(value: bool, message: str) -> None:
    if not value:
        raise HTTPException(status_code=409, detail=message)
