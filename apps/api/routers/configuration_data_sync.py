"""Prepared SQLite-only data routes; normal main.py never mounts this router."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field

from alphalab.dataio.sync import SyncRequest
from alphalab.utils.configuration_store import ConfigurationStoreError
from apps.api.routers.data_source_profiles import RQDataProfileRequest
from apps.api.routers.data_sync import (
    ConnectionTestRequest,
    RecipeExecutionRequest,
    RecipeSourceRequest,
)
from apps.api.routers.model_providers import PrivateValidationRoute, ProfileSelectionRequest
from apps.api.services.configuration_profile_service import _guard

router = APIRouter(route_class=PrivateValidationRoute)


async def data_binding(request: Request):
    value = getattr(request.app.state, "configuration_data", None)
    if value is None or value._closed:
        raise ConfigurationStoreError("STORE_UNAVAILABLE")
    with value.backend.borrow():
        if value._closed:
            raise ConfigurationStoreError("STORE_UNAVAILABLE")
        yield value


class StoredRQRequest(RQDataProfileRequest):
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


@router.get("/api/data-source-profiles")
def rq_profiles(data=Depends(data_binding)):
    return data.rq_settings()


@router.put("/api/data-source-profiles")
def rq_save(request: StoredRQRequest, data=Depends(data_binding)):
    return data.rq_settings(
        profile=request.model_dump(exclude={"activate", "clear_password", "expected_revision"}),
        activate=request.activate,
        clear_password=request.clear_password,
        expected_revision=request.expected_revision,
    )


@router.put("/api/data-source-profiles/selection")
def rq_select(request: ProfileSelectionRequest, data=Depends(data_binding)):
    return data.rq_settings(
        selection=request.model_dump(exclude={"expected_revision"}, exclude_none=True),
        expected_revision=request.expected_revision,
    )


@router.post("/api/data-sync/connection-test")
def connection(request: ConnectionTestRequest, data=Depends(data_binding)):
    return data.connection_test(request.template_id)


@router.post("/api/data-sync/plan")
def plan(request: SyncRequest, data=Depends(data_binding)):
    return data.plan(request)


@router.post("/api/data-sync/jobs", status_code=202)
def submit(request: SyncRequest, data=Depends(data_binding)):
    return data.public_job(_guard(data.manager.submit)(request))


@router.get("/api/data-sync/jobs")
def jobs(limit: int = Query(default=50, ge=1, le=200), data=Depends(data_binding)):
    return [
        value
        for item in data.manager.operations.list_jobs(limit=limit)
        if (value := data.public_job(item)) is not None
    ]


def _job(data, identifier):
    value = data.public_job(data.manager.operations.get_job(identifier))
    if value is None:
        raise HTTPException(404, "Sync job not found.")
    return value


@router.get("/api/data-sync/jobs/{job_id}")
def job(job_id: str, data=Depends(data_binding)):
    return _job(data, job_id)


@router.post("/api/data-sync/jobs/{job_id}/cancel")
def cancel(job_id: str, data=Depends(data_binding)):
    _job(data, job_id)
    return data.public_job(data.manager.cancel(job_id))


@router.put("/api/data-sync/recipes/{project_id}")
def save_recipe(project_id: str, request: RecipeSourceRequest, data=Depends(data_binding)):
    if not request.confirm_write:
        raise HTTPException(409, "Saving Python data recipe source requires confirmation.")
    return data.save_recipe(
        project_id, request.source, expected_source_sha256=request.expected_source_sha256
    )


def _source(data, project_id):
    value = data.manager.operations.get_recipe_draft(project_id)
    if value is None:
        raise HTTPException(404, "Data recipe draft not found.")
    return value["source"]


@router.post("/api/data-sync/recipes/{project_id}/plan")
def plan_recipe(project_id: str, request: RecipeExecutionRequest, data=Depends(data_binding)):
    if not request.confirm_python_execution:
        raise HTTPException(409, "Executing Python data recipe requires confirmation.")
    return data.plan_recipe(_source(data, project_id))


@router.post("/api/data-sync/recipes/{project_id}/jobs", status_code=202)
def submit_recipe(project_id: str, request: RecipeExecutionRequest, data=Depends(data_binding)):
    if not request.confirm_python_execution:
        raise HTTPException(409, "Executing Python data recipe requires confirmation.")
    return data.public_job(
        _guard(data.manager.submit_recipe)(_source(data, project_id), project_id=project_id)
    )
