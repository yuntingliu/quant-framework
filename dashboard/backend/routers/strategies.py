"""Strategy template endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from dashboard.backend.services.framework_service import (
    clone_strategy,
    delete_strategy,
    export_strategy,
    get_strategy_template,
    import_strategy,
    list_strategy_templates,
    save_strategy,
    validate_strategy_yaml,
)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


class StrategyYamlRequest(BaseModel):
    yaml: str = Field(min_length=1, max_length=100_000)


class StrategyCloneRequest(BaseModel):
    target_id: str = Field(min_length=2, max_length=64)


class StrategyImportRequest(StrategyYamlRequest):
    overwrite: bool = False


@router.get("")
def strategies() -> list[dict]:
    return list_strategy_templates()


@router.post("/validate")
def validate_strategy(request: StrategyYamlRequest) -> dict:
    try:
        return validate_strategy_yaml(request.yaml)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import", status_code=201)
def import_yaml(request: StrategyImportRequest) -> dict:
    try:
        return import_strategy(request.yaml, overwrite=request.overwrite)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="strategy already exists") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{strategy_id}/export")
def export_yaml(strategy_id: str) -> Response:
    try:
        yaml_text = export_strategy(strategy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=yaml_text,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{strategy_id}.yaml"'},
    )


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


@router.get("/{strategy_id}")
def strategy(strategy_id: str) -> dict:
    try:
        item = get_strategy_template(strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return item


@router.put("/{strategy_id}")
def update(strategy_id: str, request: StrategyYamlRequest) -> dict:
    try:
        return save_strategy(strategy_id, request.yaml)
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
