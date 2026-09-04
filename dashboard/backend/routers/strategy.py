"""Canonical Strategy SDK v1 project, source, evaluation, and preview API."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alphalab.dataio import MissingDataError
from alphalab.strategy.sdk_runtime import SdkRuntimeError
from alphalab.strategy.source import StrategySourceError
from dashboard.backend.services import strategy_service
from dashboard.backend.services.data_service import _profile_range, research_dataset_schema

router = APIRouter(prefix="/api/strategy", tags=["strategy-sdk-v1"])


class TemplateFunctionReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entrypoint_id: str = Field(min_length=1, max_length=100)
    function_source: str = Field(min_length=1, max_length=100_000)


class ProjectFactorTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(min_length=1, max_length=100)
    factor_id: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    label: str | None = Field(default=None, min_length=1, max_length=100)
    parameter_values: dict[str, Any] = Field(default_factory=dict, max_length=30)
    body: str | None = Field(default=None, min_length=1, max_length=100_000)

    @model_validator(mode="after")
    def require_custom_factor_edits(self) -> "ProjectFactorTemplate":
        if self.template_id == "custom_factor" and (
            self.factor_id is None or self.body is None
        ):
            raise ValueError("custom_factor requires factor_id and body")
        return self


class ProjectRecipeParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    symbols: list[str] | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_date_range(self) -> "ProjectRecipeParameters":
        if self.start is not None:
            date.fromisoformat(self.start)
        if self.end is not None:
            date.fromisoformat(self.end)
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("recipe start must not be after end")
        return self


class ProjectValidationParameterEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entrypoint_id: str = Field(min_length=1, max_length=100)
    parameter: str = Field(min_length=1, max_length=100)
    value: Any


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    template_id: Literal["common_stock_selection", "etf_rotation"]
    function_replacements: list[TemplateFunctionReplacement] = Field(
        default_factory=list, max_length=20
    )
    data_requirements: dict[
        Literal["bars", "fundamentals", "instruments", "daily_factors", "index_components"],
        list[str],
    ] = Field(default_factory=dict, max_length=5)
    factors: list[ProjectFactorTemplate] | None = Field(default=None, max_length=100)
    recipe_parameters: ProjectRecipeParameters | None = None
    validation_parameter_edits: list[ProjectValidationParameterEdit] = Field(
        default_factory=list, max_length=100
    )
    profile: Literal["runtime"] = "runtime"
    settings: dict[str, Any] = Field(default_factory=dict)
    confirm_save: bool
    confirm_python_execution: bool


class CloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=2, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    confirm_save: bool
    confirm_python_execution: bool


class TemplateMigrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: Literal["common_stock_selection"]
    expected_source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    confirm_write: bool
    confirm_python_execution: bool


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=300_000)
    expected_source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    confirm_write: bool
    confirm_python_execution: bool


class MetadataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    profile: Literal["runtime"]
    settings: dict[str, Any]
    confirm_write: bool


class SaveRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    confirm_save: bool
    confirm_python_execution: bool


class SourceValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=300_000)


class AddFactorTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    confirm_write: bool
    confirm_python_execution: bool


class AddFactorSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=100_000)
    expected_source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    confirm_write: bool
    confirm_python_execution: bool


class StructuredEditItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["parameter", "schedule", "factor_blend"]
    entrypoint_id: str = Field(min_length=1, max_length=100)
    parameter: str | None = None
    value: Any = None
    frequency: Literal["daily", "weekly", "monthly"] | None = None
    selector: Literal["every", "first_trading_day", "last_trading_day"] | None = None
    at: Literal["open", "close"] | None = None
    factor_weights: dict[str, float] | None = None
    normalization: Literal["raw", "rank", "zscore"] | None = None

    @model_validator(mode="after")
    def required_operation_fields(self):
        if self.operation == "parameter" and not self.parameter:
            raise ValueError("parameter is required for a parameter edit")
        if self.operation == "schedule" and (not self.frequency or not self.at):
            raise ValueError("frequency and at are required for a schedule edit")
        if self.operation == "factor_blend" and (not self.factor_weights or not self.normalization):
            raise ValueError("factor_weights and normalization are required")
        return self


class StructuredEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal[
        "parameter", "schedule", "factor_blend", "replace_function", "delete_function", "batch"
    ]
    entrypoint_id: str | None = Field(default=None, min_length=1, max_length=100)
    parameter: str | None = None
    value: Any = None
    frequency: Literal["daily", "weekly", "monthly"] | None = None
    selector: Literal["every", "first_trading_day", "last_trading_day"] | None = None
    at: Literal["open", "close"] | None = None
    factor_weights: dict[str, float] | None = None
    normalization: Literal["raw", "rank", "zscore"] | None = None
    function_source: str | None = Field(default=None, max_length=100_000)
    edits: list[StructuredEditItem] = Field(default_factory=list, max_length=100)
    expected_source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    confirm_write: bool
    confirm_python_execution: bool

    @model_validator(mode="after")
    def required_operation_fields(self):
        if self.operation == "batch":
            if not self.edits:
                raise ValueError("edits are required for a batch edit")
            return self
        if not self.entrypoint_id:
            raise ValueError("entrypoint_id is required")
        if self.operation == "parameter" and not self.parameter:
            raise ValueError("parameter is required for a parameter edit")
        if self.operation == "schedule" and (not self.frequency or not self.at):
            raise ValueError("frequency and at are required for a schedule edit")
        if self.operation == "factor_blend" and (not self.factor_weights or not self.normalization):
            raise ValueError("factor_weights and normalization are required")
        if self.operation == "replace_function" and not self.function_source:
            raise ValueError("function_source is required")
        return self


class StructuredEditPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[StructuredEditItem] = Field(min_length=1, max_length=100)
    expected_source_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class InsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["field", "factor", "source"]
    value: str = Field(min_length=1, max_length=100_000)
    cursor: int = Field(ge=0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    confirm_write: bool
    confirm_python_execution: bool


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["signal", "portfolio", "execution"] = "execution"
    profile: Literal["runtime"] = "runtime"
    as_of_date: date | None = None
    revision: int | None = Field(default=None, ge=1)
    confirm_python_execution: bool


class FactorSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: Literal["runtime"] = "runtime"
    as_of_date: date
    revision: int | None = Field(default=None, ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    confirm_python_execution: bool


class FactorHistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: Literal["runtime"] = "runtime"
    start_date: date
    end_date: date
    revision: int | None = Field(default=None, ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    frequency: Literal["daily", "weekly", "monthly"] = "monthly"
    confirm_python_execution: bool


def _confirmed(value: bool, message: str) -> None:
    if value is not True:
        raise HTTPException(status_code=409, detail=message)


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).splitlines()[0] if str(exc) else "Strategy request failed"
    message = re.sub(r"[A-Za-z]:\\[^\s\"']+", "<internal-path>", message)
    message = re.sub(
        r"(?<![:/\w])/(?!/)(?:[^/\s]+/)+[^\s\"']+",
        "<internal-path>",
        message,
    )
    message = re.sub(r"\b[a-fA-F0-9]{40,64}\b", "<internal-id>", message)
    return message[:500]


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="strategy project or revision not found")
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail="strategy project already exists")
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=409, detail=_safe_error_message(exc))
    if isinstance(exc, RuntimeError) and "changed since" in str(exc):
        return HTTPException(status_code=409, detail=_safe_error_message(exc))
    if isinstance(exc, MissingDataError):
        return HTTPException(
            status_code=503,
            detail={
                "code": "INSUFFICIENT_MARKET_STATE",
                "message": "Runtime data coverage is insufficient for this request.",
            },
        )
    if isinstance(exc, StrategySourceError):
        return HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_STRATEGY_SOURCE",
                "message": _safe_error_message(exc),
                "phase": exc.phase,
            },
        )
    if isinstance(exc, SdkRuntimeError):
        return HTTPException(
            status_code=422,
            detail={
                "code": "STRATEGY_EXECUTION_FAILED",
                "message": _safe_error_message(exc),
                "phase": exc.phase,
                "entrypoint_id": exc.entrypoint_id,
                "event": exc.event,
                "as_of": exc.as_of,
                "committed": exc.committed,
            },
        )
    return HTTPException(status_code=422, detail=_safe_error_message(exc))


@router.get("/projects")
def projects() -> list[dict[str, Any]]:
    return strategy_service.list_projects()


@router.get("/fields")
def fields(profile: Literal["runtime"] = "runtime") -> dict[str, Any]:
    try:
        datasets = {}
        for dataset in ("market_bars", "fundamentals"):
            datasets[dataset] = [
                {
                    "name": field.name,
                    "data_type": field.data_type,
                    "nullable": field.nullable,
                }
                for field in research_dataset_schema(profile, dataset)
                if field.name not in {"date", "symbol", "quarter", "available_date"}
            ]
        start, end = _profile_range(profile)
        return {"profile": profile, "start_date": start, "end_date": end, "datasets": datasets}
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/factor-templates")
def factor_templates() -> dict[str, Any]:
    return strategy_service.factor_template_catalog()


@router.get("/project-templates")
def project_templates() -> dict[str, Any]:
    return strategy_service.strategy_project_template_catalog()


@router.get("/projects/{project_id}")
def project(project_id: str) -> dict[str, Any]:
    item = strategy_service.get_project(project_id)
    if item is None:
        raise HTTPException(status_code=404, detail="strategy project not found")
    return item


@router.post("/projects", status_code=201)
def create_project(request: CreateProjectRequest) -> dict[str, Any]:
    _confirmed(request.confirm_save, "saving a strategy project requires confirmation")
    _confirmed(
        request.confirm_python_execution, "strategy validation executes trusted local Python"
    )
    try:
        return strategy_service.create_project(
            request.model_dump(exclude={"confirm_save", "confirm_python_execution"})
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/clone", status_code=201)
def clone_project(project_id: str, request: CloneRequest) -> dict[str, Any]:
    _confirmed(request.confirm_save, "saving a cloned strategy requires confirmation")
    _confirmed(
        request.confirm_python_execution, "strategy validation executes trusted local Python"
    )
    try:
        return strategy_service.clone_project(project_id, request.target_id, request.name)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/template-migration")
def migrate_project_template(project_id: str, request: TemplateMigrationRequest) -> dict[str, Any]:
    _confirmed(request.confirm_write, "migrating strategy template components requires confirmation")
    _confirmed(
        request.confirm_python_execution,
        "saving migrated strategy source runs trusted local probes and requires confirmation",
    )
    try:
        return strategy_service.migrate_project_template(
            project_id,
            request.template_id,
            expected_source_sha256=request.expected_source_sha256,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.put("/projects/{project_id}/draft")
def save_draft(project_id: str, request: DraftRequest) -> dict[str, Any]:
    _confirmed(request.confirm_write, "updating strategy source requires confirmation")
    _confirmed(
        request.confirm_python_execution,
        "saving strategy source runs trusted local probes and requires confirmation",
    )
    try:
        return strategy_service.update_strategy_source(
            project_id,
            request.source,
            expected_source_sha256=request.expected_source_sha256,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.put("/projects/{project_id}/metadata")
def save_metadata(project_id: str, request: MetadataRequest) -> dict[str, Any]:
    _confirmed(request.confirm_write, "updating project metadata requires confirmation")
    try:
        return strategy_service.update_metadata(
            project_id,
            request.model_dump(exclude={"confirm_write"}),
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/revisions", status_code=201)
def create_revision(project_id: str, request: SaveRevisionRequest) -> dict[str, Any]:
    _confirmed(request.confirm_save, "saving an immutable strategy revision requires confirmation")
    _confirmed(request.confirm_python_execution, "strategy probes execute trusted local Python")
    try:
        return strategy_service.save_revision(
            project_id,
            expected_source_sha256=request.expected_source_sha256,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/projects/{project_id}/revisions")
def revisions(project_id: str) -> list[dict[str, Any]]:
    try:
        return strategy_service.list_revisions(project_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/projects/{project_id}/revisions/{revision}")
def revision(project_id: str, revision: int) -> dict[str, Any]:
    item = strategy_service.get_revision(project_id, revision)
    if item is None:
        raise HTTPException(status_code=404, detail="strategy revision not found")
    return item


@router.post("/validate")
def validate(request: SourceValidationRequest) -> dict[str, Any]:
    try:
        return strategy_service.validate_source(request.source)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/projects/{project_id}/entrypoints/{entrypoint_id}/source")
def entrypoint_source(project_id: str, entrypoint_id: str) -> dict[str, Any]:
    try:
        return strategy_service.get_entrypoint_source(project_id, entrypoint_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/edits")
def edit(project_id: str, request: StructuredEditRequest) -> dict[str, Any]:
    _confirmed(request.confirm_write, "updating strategy source requires confirmation")
    _confirmed(
        request.confirm_python_execution,
        "saving strategy source runs trusted local probes and requires confirmation",
    )
    try:
        return strategy_service.structured_edit(
            project_id,
            request.model_dump(
                exclude={"confirm_write", "confirm_python_execution"},
                exclude_none=True,
            ),
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/edits/preview")
def preview_edits(project_id: str, request: StructuredEditPreviewRequest) -> dict[str, Any]:
    try:
        return strategy_service.preview_structured_edits(
            project_id,
            request.model_dump(exclude_none=True),
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/factor-templates/{template_id}")
def add_factor_template(
    project_id: str, template_id: str, request: AddFactorTemplateRequest
) -> dict[str, Any]:
    _confirmed(request.confirm_write, "adding a factor template requires confirmation")
    _confirmed(
        request.confirm_python_execution,
        "saving factor source runs trusted local probes and requires confirmation",
    )
    try:
        return strategy_service.add_project_factor_template(
            project_id,
            template_id,
            expected_source_sha256=request.expected_source_sha256,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/factors", status_code=201)
def add_factor_source(project_id: str, request: AddFactorSourceRequest) -> dict[str, Any]:
    _confirmed(request.confirm_write, "adding factor source requires confirmation")
    _confirmed(
        request.confirm_python_execution,
        "saving factor source runs trusted local probes and requires confirmation",
    )
    try:
        return strategy_service.add_project_factor_source(
            project_id,
            request.source,
            expected_source_sha256=request.expected_source_sha256,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/insertions")
def insert(project_id: str, request: InsertRequest) -> dict[str, Any]:
    _confirmed(request.confirm_write, "updating strategy source requires confirmation")
    _confirmed(
        request.confirm_python_execution,
        "saving strategy source runs trusted local probes and requires confirmation",
    )
    try:
        return strategy_service.insertion(
            project_id,
            request.model_dump(exclude={"confirm_write", "confirm_python_execution"}),
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/preview")
def preview(project_id: str, request: PreviewRequest) -> dict[str, Any]:
    _confirmed(request.confirm_python_execution, "preview executes trusted local Python")
    try:
        return strategy_service.preview_project(
            project_id,
            operation=request.operation,
            profile=request.profile,
            as_of_date=request.as_of_date.isoformat() if request.as_of_date else None,
            revision=request.revision,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/factors/{factor_id}/snapshot")
def snapshot(project_id: str, factor_id: str, request: FactorSnapshotRequest) -> dict[str, Any]:
    _confirmed(request.confirm_python_execution, "factor evaluation executes trusted local Python")
    try:
        return strategy_service.factor_snapshot(
            project_id,
            factor_id,
            profile=request.profile,
            as_of_date=request.as_of_date.isoformat(),
            revision=request.revision,
            parameters=request.parameters,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/factors/{factor_id}/history")
def history(project_id: str, factor_id: str, request: FactorHistoryRequest) -> dict[str, Any]:
    _confirmed(request.confirm_python_execution, "factor evaluation executes trusted local Python")
    try:
        return strategy_service.factor_history(
            project_id,
            factor_id,
            profile=request.profile,
            start_date=request.start_date.isoformat(),
            end_date=request.end_date.isoformat(),
            revision=request.revision,
            parameters=request.parameters,
            frequency=request.frequency,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.delete("/projects/{project_id}", status_code=204)
def remove_project(project_id: str, confirm_delete: bool = False) -> Response:
    _confirmed(confirm_delete, "deleting a strategy project requires confirmation")
    try:
        if not strategy_service.delete_project(project_id):
            raise HTTPException(status_code=404, detail="strategy project not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate_error(exc) from exc
    return Response(status_code=204)


__all__ = ["router"]
