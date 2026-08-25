"""Application service for the non-authoritative Python Lab."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd

from alphalab.factors import list_factors
from alphalab.factors.repository import FactorDefinitionRepository
from alphalab.pipeline.models import STAGE_NAMES, normalize_id
from alphalab.python_lab import PythonLabConfig, PythonLabRuntime, validate_lab_source
from alphalab.store import ResultStore
from alphalab.strategy import FactorSpec
from dashboard.backend.services import pipeline_service
from dashboard.backend.services.data_service import _engine, _profile_range


def capabilities() -> dict[str, Any]:
    return PythonLabConfig.from_env().capabilities()


def _json_hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _project_symbols(project: dict[str, Any], profile: str) -> list[str]:
    universe = dict(project.get("settings", {}).get("universe") or {})
    configured = [str(item).strip().upper() for item in universe.get("symbols") or []]
    if configured:
        return list(dict.fromkeys(item for item in configured if item))
    pool = str(universe.get("pool") or "all")
    return list(_engine(profile).get_symbols(pool))


def build_context(
    project: dict[str, Any],
    *,
    profile: str,
    as_of_date: str | None,
    lookback_days: int,
    requested_symbols: list[str] | None,
    max_rows: int,
) -> dict[str, Any]:
    profile_start, profile_end = _profile_range(profile)
    decision = pd.Timestamp(as_of_date or profile_end).normalize()
    if decision > pd.Timestamp(profile_end):
        decision = pd.Timestamp(profile_end)
    if decision < pd.Timestamp(profile_start):
        raise ValueError("as_of_date is before the selected data profile")

    allowed = _project_symbols(project, profile)
    allowed_set = set(allowed)
    requested = [str(item).strip().upper() for item in requested_symbols or []]
    unknown = sorted(set(requested) - allowed_set)
    if unknown:
        raise ValueError(
            "requested symbols are outside the project universe: " + ", ".join(unknown[:10])
        )
    symbols = list(dict.fromkeys(requested)) if requested else allowed
    if not symbols:
        raise ValueError("the project universe has no symbols")
    if len(symbols) > 500:
        raise ValueError("Python Lab context is limited to 500 symbols per run")

    calendar_start = max(
        pd.Timestamp(profile_start),
        decision - pd.Timedelta(days=lookback_days * 2 + 30),
    )
    bars = _engine(profile).get_bars(
        symbols,
        calendar_start.strftime("%Y-%m-%d"),
        decision.strftime("%Y-%m-%d"),
        strict=False,
        use_cache=False,
    )
    if bars.empty:
        raise ValueError("no market bars are available for the selected context")
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"])
    bars["symbol"] = bars["symbol"].astype(str).str.upper()
    bars = (
        bars.sort_values(["symbol", "date"])
        .groupby("symbol", group_keys=False)
        .tail(lookback_days)
        .sort_values(["date", "symbol"])
    )
    available_rows = len(bars)
    truncated = available_rows > max_rows
    if truncated:
        # Keep the most recent observations.  Metadata makes the truncation
        # explicit so experiments cannot silently assume a complete panel.
        bars = bars.tail(max_rows)
    raw_records = json.loads(bars.to_json(orient="records", date_format="iso"))
    records: list[dict[str, Any]] = []
    for item in raw_records:
        row = dict(item)
        if row.get("date"):
            row["date"] = str(row["date"])[:10]
        records.append(row)

    return {
        "contract_version": 1,
        "profile": profile,
        "as_of_date": decision.strftime("%Y-%m-%d"),
        "project": {
            "id": project["id"],
            "name": project["name"],
            "revision": project["revision"],
            "settings": dict(project.get("settings") or {}),
            "component_manifest": list(project.get("component_manifest") or []),
            "source_sha256": project.get("source_sha256"),
        },
        "market_bars": records,
        "dataset": {
            "symbols_requested": len(symbols),
            "symbols_returned": len({str(item["symbol"]) for item in records}),
            "rows": len(records),
            "rows_before_limit": available_rows,
            "lookback_days_per_symbol": lookback_days,
            "truncated": truncated,
            "max_rows": max_rows,
        },
        "output_contract": {
            "component_candidate": {
                "stage": "selection | portfolio | execution",
                "name": "string",
                "description": "string",
                "source": "stage Python source",
                "parameters": {},
            },
            "factor_candidate": {
                "name": "identifier",
                "description": "string",
                "expression": "safe factor expression",
                "direction": "long | short",
                "winsorize": 0.01,
                "neutralize": [],
            },
        },
    }


def run(
    *,
    project_id: str,
    profile: str,
    source: str,
    as_of_date: str | None,
    lookback_days: int,
    symbols: list[str] | None,
    max_rows: int,
    confirm_python_execution: bool,
    confirm_trusted_local: bool,
    runtime: PythonLabRuntime | None = None,
) -> dict[str, Any]:
    if not confirm_python_execution:
        raise PermissionError("Python execution requires confirm_python_execution=true")
    project = pipeline_service.get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    validation = validate_lab_source(source)
    context = build_context(
        project,
        profile=profile,
        as_of_date=as_of_date,
        lookback_days=lookback_days,
        requested_symbols=symbols,
        max_rows=max_rows,
    )
    executor = runtime or PythonLabRuntime()
    runtime_kind = executor.config.kind
    if runtime_kind == "disabled":
        raise PermissionError("Python Lab is disabled by configuration")
    store = ResultStore()
    try:
        run_id = store.create_python_lab_run(
            project_id=project["id"],
            profile=profile,
            runtime_kind=runtime_kind,
            source=source,
            source_sha256=validation["sha256"],
            context=context,
            context_sha256=_json_hash(context),
        )
        try:
            result = executor.execute(
                source,
                context,
                confirm_trusted_local=confirm_trusted_local,
            )
        except Exception as exc:
            store.finish_python_lab_run(
                run_id,
                status="failed",
                stdout=str(getattr(exc, "stdout", "")),
                stderr=str(getattr(exc, "stderr", "")),
                error=str(exc),
            )
            raise
        store.finish_python_lab_run(
            run_id,
            status="succeeded",
            output=result.output,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return store.get_python_lab_run(run_id) or {}
    finally:
        store.close()


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    store = ResultStore()
    try:
        return store.list_python_lab_runs(limit)
    finally:
        store.close()


def get_run(run_id: str) -> dict[str, Any] | None:
    store = ResultStore()
    try:
        return store.get_python_lab_run(run_id)
    finally:
        store.close()


def _candidate(run_record: dict[str, Any], kind: str) -> dict[str, Any]:
    output = run_record.get("output")
    if not isinstance(output, dict):
        raise ValueError("Python Lab output is not an object")
    value = output.get(f"{kind}_candidate")
    if not isinstance(value, dict):
        raise ValueError(f"run output has no {kind}_candidate")
    return dict(value)


def promote(
    run_id: str,
    *,
    kind: str,
    target_id: str | None,
    apply_to_project: bool,
    confirm_write: bool,
) -> dict[str, Any]:
    if not confirm_write:
        raise PermissionError("promotion requires confirm_write=true")
    record = get_run(run_id)
    if record is None:
        raise KeyError(run_id)
    if record["status"] != "succeeded":
        raise ValueError("only a successful Python Lab run can be promoted")
    project = pipeline_service.get_project(record["project_id"])
    if project is None:
        raise KeyError(record["project_id"])
    if apply_to_project and not project.get("editable", False):
        raise PermissionError("built-in projects are immutable; clone before applying a candidate")
    if kind == "component":
        promoted = _promote_component(record, project, target_id, apply_to_project)
    elif kind == "factor":
        promoted = _promote_factor(record, project, target_id, apply_to_project)
    else:
        raise ValueError("promotion kind must be component or factor")
    promoted_object = promoted[kind]
    updated_project = promoted.get("project")
    store = ResultStore()
    try:
        promotion_id = store.record_python_lab_promotion(
            run_id,
            kind=kind,
            target_id=str(
                promoted_object["id"] if kind == "component" else promoted_object["name"]
            ),
            applied_to_project=apply_to_project,
            project_revision=(
                int(updated_project["revision"]) if updated_project is not None else None
            ),
        )
    finally:
        store.close()
    return {
        "run_id": run_id,
        "promotion_id": promotion_id,
        "kind": kind,
        "applied_to_project": apply_to_project,
        **promoted,
    }


def _promote_component(
    record: dict[str, Any],
    project: dict[str, Any],
    target_id: str | None,
    apply_to_project: bool,
) -> dict[str, Any]:
    candidate = _candidate(record, "component")
    stage = str(candidate.get("stage") or "")
    if stage not in STAGE_NAMES:
        raise ValueError(f"component candidate stage must be one of {STAGE_NAMES}")
    component_id = normalize_id(target_id or str(candidate.get("id") or ""), label="component id")
    if pipeline_service.get_component(component_id) is not None:
        raise FileExistsError(component_id)
    component = pipeline_service.create_component(
        {
            "component_id": component_id,
            "stage": stage,
            "name": str(candidate.get("name") or component_id)[:100],
            "description": str(candidate.get("description") or "")[:500],
            "source": str(candidate.get("source") or ""),
            "parameters": dict(candidate.get("parameters") or {}),
            "notes": f"promoted from Python Lab run {record['id']}",
        }
    )
    updated_project = None
    if apply_to_project:
        refs = dict(project["components"])
        refs[stage] = {"component_id": component["id"], "version": component["version"]}
        updated_project = pipeline_service.update_project(
            project["id"],
            {
                "name": project["name"],
                "description": project["description"],
                "components": refs,
                "settings": dict(project.get("settings") or {}),
            },
        )
    return {"component": component, "project": updated_project}


def _promote_factor(
    record: dict[str, Any],
    project: dict[str, Any],
    target_id: str | None,
    apply_to_project: bool,
) -> dict[str, Any]:
    candidate = _candidate(record, "factor")
    name = str(target_id or candidate.get("name") or "").strip()
    factor = FactorSpec(
        name=name,
        source="expression",
        expression=str(candidate.get("expression") or ""),
        direction=str(candidate.get("direction") or "long"),
        weight=float(candidate.get("weight", 1.0)),
        winsorize=float(candidate.get("winsorize", 0.01)),
        neutralize=tuple(candidate.get("neutralize") or ()),
    )
    repository = FactorDefinitionRepository()
    try:
        if repository.get(name) is not None:
            raise FileExistsError(name)
        saved = repository.save(
            name,
            description=str(candidate.get("description") or ""),
            expression=factor.expression or "",
            direction=factor.direction,
            winsorize=factor.winsorize,
            neutralize=factor.neutralize,
            reserved_names=(item.name for item in list_factors()),
        )
    finally:
        repository.close()
    updated_project = None
    if apply_to_project:
        settings = dict(project.get("settings") or {})
        current = [dict(item) for item in settings.get("factors") or []]
        current = [item for item in current if item.get("name") != factor.name]
        current.append(
            {
                "name": factor.name,
                "weight": factor.weight,
                "source": factor.source,
                "expression": factor.expression,
                "direction": factor.direction,
                "winsorize": factor.winsorize,
                "neutralize": list(factor.neutralize),
            }
        )
        settings["factors"] = current
        updated_project = pipeline_service.update_project(
            project["id"],
            {
                "name": project["name"],
                "description": project["description"],
                "components": dict(project["components"]),
                "settings": settings,
            },
        )
    return {"factor": saved, "project": updated_project}


__all__ = [
    "build_context",
    "capabilities",
    "get_run",
    "list_runs",
    "promote",
    "run",
]
