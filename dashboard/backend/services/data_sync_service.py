"""Thin dashboard service over the runtime data control plane."""

from __future__ import annotations

import os
import uuid
from datetime import date
from functools import lru_cache
from importlib.util import find_spec
from importlib.metadata import PackageNotFoundError, version

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.errors import DataLoadError
from alphalab.dataio.recipes import (
    RQDATA_PYTHON_DOCS_URL,
    builtin_recipe_catalog,
    execute_data_recipe,
    inspect_data_recipe_source,
    migrate_legacy_builtin_recipe,
    render_builtin_recipe,
    update_recipe_parameters as project_recipe_parameters,
)
from alphalab.dataio.quality import validate_all, validate_dataset
from alphalab.dataio.providers.rq import RQDataClient
from alphalab.dataio.rq_templates import (
    get_rq_sync_template,
    list_rq_sync_templates,
)
from alphalab.dataio.sync import SyncJobManager, SyncRequest, build_sync_plan
from alphalab.strategy.repository import normalize_project_id
from alphalab.tools import create_data_tool_registry
from alphalab.utils.env import load_env_files
from dashboard.backend.services import strategy_service

_RQ_KEYS = ("RQ_USER", "RQ_PASSWORD", "RQ_HOST")


@lru_cache(maxsize=1)
def get_job_manager() -> SyncJobManager:
    return SyncJobManager()


def get_health() -> dict:
    load_env_files()
    missing = [key for key in _RQ_KEYS if not os.environ.get(key, "").strip()]
    installed = find_spec("rqdatac") is not None
    credentials_configured = not missing
    ready = installed and credentials_configured
    jobs = get_job_manager().operations.list_jobs(limit=1)
    latest = jobs[0] if jobs else None
    rq_status = (
        "not_installed"
        if not installed
        else "not_configured" if not credentials_configured else "configured"
    )
    if ready and latest and latest["status"] == "failed":
        rq_status = "unavailable"
    return {
        "status": "ok",
        "runtime": DataCatalog().summary(),
        "rq": {
            "status": rq_status,
            "installed": installed,
            "configured": credentials_configured,
            "ready": ready,
            "missing": missing,
            "connected": False,
            "connection_test_required": ready,
            "last_error": latest.get("error") if latest and latest["status"] == "failed" else None,
        },
        "realtime": {"status": "not_configured"},
        "tools": {
            "status": "ready",
            "count": len(create_data_tool_registry().describe()),
        },
        "planner": {"status": "not_configured"},
    }


def templates() -> dict:
    return {
        "templates": [item.to_dict() for item in list_rq_sync_templates()],
        "python_sdk": {
            "version": 1,
            "import": "alphalab.data_sdk.v1",
            "execution": "trusted_local_python",
            "uses_data_engine_contracts": True,
        },
    }


def recipe_workspace(project_id: str) -> dict:
    project = _project_id(project_id)
    manager = get_job_manager()
    bounds = _recipe_bounds()
    draft = manager.operations.get_recipe_draft(project)
    if draft is None:
        source = render_builtin_recipe(
            "rq.a_share_daily",
            start=bounds["start"],
            end=bounds["end"],
        )
        draft = manager.operations.save_recipe_draft(project, source)
    else:
        migrated = migrate_legacy_builtin_recipe(draft["source"])
        if migrated != draft["source"]:
            draft = manager.operations.save_recipe_draft(
                project,
                migrated,
                expected_source_sha256=draft["source_sha256"],
            )
    return {
        "draft": _recipe_draft(draft),
        "templates": [
            *_built_in_recipe_templates(bounds),
            *[
                {
                    "id": item["id"],
                    "label": item["name"],
                    "description": item["description"],
                    "kind": "custom",
                    "source_sha256": item["source_sha256"],
                    "updated_at": item["updated_at"],
                }
                for item in manager.operations.list_recipe_templates()
            ],
        ],
        "bounds": bounds,
        "docs_url": RQDATA_PYTHON_DOCS_URL,
        "execution": "trusted_local_python",
    }


def save_recipe_source(
    project_id: str,
    source: str,
    *,
    expected_source_sha256: str | None,
) -> dict:
    project = _project_id(project_id)
    inspect_data_recipe_source(source)
    draft = get_job_manager().operations.save_recipe_draft(
        project,
        source,
        expected_source_sha256=expected_source_sha256,
    )
    return _recipe_draft(draft)


def apply_recipe_template(
    project_id: str,
    template_id: str,
    *,
    expected_source_sha256: str | None,
) -> dict:
    project = _project_id(project_id)
    operations = get_job_manager().operations
    current = operations.get_recipe_draft(project)
    if current is None:
        recipe_workspace(project)
        current = operations.get_recipe_draft(project)
    assert current is not None
    built_in = {item["id"] for item in _built_in_recipe_templates(_recipe_bounds())}
    if template_id in built_in:
        parameters = _editable_recipe_parameters(current["source"])
        bounds = _recipe_bounds()
        source = render_builtin_recipe(
            template_id,
            start=str(parameters.get("start") or bounds["start"]),
            end=str(parameters.get("end") or bounds["end"]),
            symbols=_symbol_list(parameters.get("symbols")),
        )
    else:
        template = operations.get_recipe_template(template_id)
        if template is None:
            raise KeyError(template_id)
        source = template["source"]
    inspect_data_recipe_source(source)
    return _recipe_draft(
        operations.save_recipe_draft(
            project,
            source,
            expected_source_sha256=expected_source_sha256,
        )
    )


def update_recipe_parameters(
    project_id: str,
    *,
    start: str,
    end: str,
    symbols: list[str] | None,
    expected_source_sha256: str | None,
) -> dict:
    project = _project_id(project_id)
    bounds = _recipe_bounds()
    normalized_start = date.fromisoformat(start)
    normalized_end = date.fromisoformat(end)
    if normalized_start > normalized_end:
        raise ValueError("start must be on or before end")
    if normalized_start < date.fromisoformat(
        bounds["start"]
    ) or normalized_end > date.fromisoformat(bounds["end"]):
        raise ValueError(f"recipe dates must stay within {bounds['start']} to {bounds['end']}")
    operations = get_job_manager().operations
    current = operations.get_recipe_draft(project)
    if current is None:
        raise KeyError(project)
    editable = {
        item.name
        for item in inspect_data_recipe_source(current["source"]).parameters
        if item.editable
    }
    values = {
        name: value
        for name, value in {
            "start": start,
            "end": end,
            "symbols": tuple(symbols) if symbols else None,
        }.items()
        if name in editable
    }
    if not values:
        raise ValueError("this recipe does not expose form-editable start, end, or symbols")
    source, _ = project_recipe_parameters(
        current["source"],
        values,
    )
    return _recipe_draft(
        operations.save_recipe_draft(
            project,
            source,
            expected_source_sha256=expected_source_sha256,
        )
    )


def save_custom_recipe_template(project_id: str, *, name: str, description: str) -> dict:
    project = _project_id(project_id)
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("custom recipe template name must not be blank")
    operations = get_job_manager().operations
    draft = operations.get_recipe_draft(project)
    if draft is None:
        raise KeyError(project)
    inspect_data_recipe_source(draft["source"])
    template_id = f"custom.{uuid.uuid4().hex[:12]}"
    item = operations.save_recipe_template(
        template_id,
        name=normalized_name,
        description=description.strip(),
        source=draft["source"],
    )
    return {
        "id": item["id"],
        "label": item["name"],
        "description": item["description"],
        "kind": "custom",
        "source_sha256": item["source_sha256"],
        "updated_at": item["updated_at"],
    }


def delete_custom_recipe_template(template_id: str) -> bool:
    if not str(template_id).startswith("custom."):
        raise ValueError("built-in recipe templates cannot be deleted")
    return get_job_manager().operations.delete_recipe_template(template_id)


def plan_recipe(project_id: str) -> dict:
    project = _project_id(project_id)
    draft = get_job_manager().operations.get_recipe_draft(project)
    if draft is None:
        raise KeyError(project)
    inspection = inspect_data_recipe_source(draft["source"])
    parameters = {item.name: item.default for item in inspection.parameters if item.editable}
    execution = execute_data_recipe(draft["source"], mode="plan")
    sync_request = execution.get("sync_request")
    base = {
        "kind": "python_recipe",
        "project_id": project,
        "template_id": inspection.template_id,
        "requested_start": parameters.get("start"),
        "requested_end": parameters.get("end"),
        "symbol_count": (
            len(parameters["symbols"])
            if isinstance(parameters.get("symbols"), (list, tuple))
            else None
        ),
        "source_sha256": execution["source_sha256"],
        "stdout": execution["stdout"],
        "stderr": execution["stderr"],
        "planned": execution["planned"],
        "published": execution["published"],
        "outputs": execution["outputs"],
    }
    if sync_request is None:
        return {
            **base,
            "steps": execution["planned"] or execution["published"],
            "estimated_batches": None,
        }
    request = SyncRequest.model_validate(sync_request)
    return {**build_sync_plan(request), **base}


def submit_recipe(project_id: str) -> dict:
    project = _project_id(project_id)
    draft = get_job_manager().operations.get_recipe_draft(project)
    if draft is None:
        raise KeyError(project)
    return get_job_manager().submit_recipe(draft["source"], project_id=project)


def test_connection(template_id: str) -> dict:
    template = get_rq_sync_template(template_id)
    client = RQDataClient.from_env()
    rq = client.connect()
    try:
        latest = rq.get_latest_trading_date(market=template.market)
    except Exception as exc:
        raise DataLoadError(
            f"RQData connection succeeded but calendar probe failed for {template.market}"
        ) from exc
    try:
        sdk_version = version("rqdatac")
    except PackageNotFoundError:
        sdk_version = "unknown"
    return {
        "status": "connected",
        "connected": True,
        "template_id": template.id,
        "market": template.market,
        "instrument_types": list(template.instrument_types),
        "latest_trading_date": str(latest),
        "rqdatac_version": sdk_version,
    }


def catalog() -> dict:
    return DataCatalog().summary()


def plan(request: SyncRequest) -> dict:
    return build_sync_plan(request)


def submit(request: SyncRequest) -> dict:
    return get_job_manager().submit(request)


def jobs(limit: int = 50) -> list[dict]:
    return get_job_manager().operations.list_jobs(limit=limit)


def job(job_id: str) -> dict | None:
    return get_job_manager().operations.get_job(job_id)


def cancel(job_id: str) -> dict | None:
    return get_job_manager().cancel(job_id)


def validate(dataset: str | None = None) -> dict | list[dict]:
    return validate_dataset(dataset) if dataset else validate_all()


def _recipe_bounds() -> dict[str, str]:
    bars = DataCatalog().status("rq.bars")
    if bars["status"] == "ready" and bars["date_start"] and bars["date_end"]:
        return {"start": bars["date_start"], "end": bars["date_end"]}
    today = date.today()
    try:
        fallback_start = today.replace(year=today.year - 5)
    except ValueError:
        fallback_start = today.replace(year=today.year - 5, day=28)
    return {"start": fallback_start.isoformat(), "end": today.isoformat()}


def _project_id(project_id: str) -> str:
    project = normalize_project_id(project_id)
    if strategy_service.get_project(project) is None:
        raise KeyError(project)
    return project


def _built_in_recipe_templates(bounds: dict[str, str]) -> list[dict]:
    return [
        {
            "id": item["id"],
            "label": item["label"],
            "description": item["description"],
            "kind": "built_in",
            "market": item["market"],
            "instrument_types": list(item["instrument_types"]),
            "datasets": list(item["datasets"]),
            "scope": item["scope"],
        }
        for item in builtin_recipe_catalog(start=bounds["start"], end=bounds["end"])
    ]


def _recipe_draft(draft: dict) -> dict:
    inspection = inspect_data_recipe_source(draft["source"])
    return {
        "project_id": draft["project_id"],
        "source": draft["source"],
        "source_sha256": draft["source_sha256"],
        "updated_at": draft["updated_at"],
        "inspection": inspection.to_dict(),
    }


def _editable_recipe_parameters(source: str) -> dict[str, object]:
    inspection = inspect_data_recipe_source(source)
    return {item.name: item.default for item in inspection.parameters if item.editable}


def _symbol_list(value: object) -> list[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    return [str(item) for item in value]
