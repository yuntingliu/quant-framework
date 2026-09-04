"""Application service for canonical Strategy SDK v1 projects and runs."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any, Mapping

import pandas as pd

from alphalab.analytics import (
    FACTOR_NAMES,
    equal_weight_benchmark,
)
from alphalab.dataio import MissingDataError
from alphalab.dataio.runtime import RuntimeStore
from alphalab.provenance import build_research_provenance
from alphalab.store import ResultStore
from alphalab.strategy.engine import (
    evaluate_factor_history,
    evaluate_factor_snapshot,
    preflight_strategy_backtest,
    preview_strategy,
    run_strategy_backtest,
)
from alphalab.strategy.factor_templates import (
    get_factor_template,
    install_factor_template,
    list_factor_templates,
)
from alphalab.strategy.repository import StrategyRepository
from alphalab.strategy.source import (
    SourceInspection,
    delete_registered_function,
    factor_dependency_snippet,
    factor_field_snippet,
    insert_source,
    inspect_strategy_source,
    registered_function_source,
    replace_registered_function,
    split_strategy_source,
    update_parameter_default,
    update_signal_factor_blend,
    update_signal_schedule,
)
from alphalab.validation.repository import ValidationRepository
from alphalab.validation.runtime import execute_validation
from dashboard.backend.services.data_service import _engine, _profile_range


def repository() -> StrategyRepository:
    return StrategyRepository()


def list_projects() -> list[dict[str, Any]]:
    repo = repository()
    try:
        return repo.list_projects()
    finally:
        repo.close()


def get_project(project_id: str) -> dict[str, Any] | None:
    repo = repository()
    try:
        return repo.get_project(project_id)
    finally:
        repo.close()


def create_project(payload: Mapping[str, Any]) -> dict[str, Any]:
    repo = repository()
    try:
        project = repo.create_project_from_units(**dict(payload))
        validation_repo = ValidationRepository(repo.path)
        try:
            validation_repo.get_or_create(project["id"])
        finally:
            validation_repo.close()
        return project
    finally:
        repo.close()


def clone_project(project_id: str, target_id: str, name: str | None) -> dict[str, Any]:
    repo = repository()
    try:
        project = repo.clone_project(project_id, target_id, name=name)
        validation_repo = ValidationRepository(repo.path)
        try:
            validation_repo.clone_source(project_id, project["id"])
        finally:
            validation_repo.close()
        return project
    finally:
        repo.close()


def update_draft(
    project_id: str,
    source: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.update_draft(
            project_id,
            source,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        repo.close()


def update_strategy_source(
    project_id: str,
    source: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.update_strategy_source(
            project_id,
            source,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        repo.close()


def add_project_factor_source(
    project_id: str,
    source: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    before = {
        item["id"] for item in project["inspection"]["entrypoints"] if item["kind"] == "factor"
    }
    repo = repository()
    try:
        updated = repo.add_factor_source(
            project_id,
            source,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        repo.close()
    factor = next(
        item
        for item in updated["inspection"]["entrypoints"]
        if item["kind"] == "factor" and item["id"] not in before
    )
    return {"project": updated, "factor": factor}


def update_metadata(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.update_metadata(project_id, **dict(payload))
    finally:
        repo.close()


def save_revision(
    project_id: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.save_revision(
            project_id,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        repo.close()


def list_revisions(project_id: str) -> list[dict[str, Any]]:
    repo = repository()
    try:
        return repo.list_packages(project_id)
    finally:
        repo.close()


def get_revision(project_id: str, revision: int) -> dict[str, Any] | None:
    repo = repository()
    try:
        return repo.get_package(project_id, revision)
    finally:
        repo.close()


def delete_project(project_id: str) -> bool:
    repo = repository()
    try:
        return repo.delete_project(project_id)
    finally:
        repo.close()


def validate_source(source: str) -> dict[str, Any]:
    return inspect_strategy_source(source).to_dict()


def factor_template_catalog() -> dict[str, Any]:
    return {"templates": [item.to_dict() for item in list_factor_templates()]}


def add_project_factor_template(
    project_id: str,
    template_id: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    if expected_source_sha256 and expected_source_sha256 != project["draft_source_sha256"]:
        raise RuntimeError("draft changed since it was inspected")
    existing_factor_ids = {
        item.id
        for item in inspect_strategy_source(project["draft_source"]).entrypoints
        if item.kind == "factor"
    }
    updated, inspection = install_factor_template(project["draft_source"], template_id=template_id)
    updated_project = update_draft(
        project_id,
        updated,
        expected_source_sha256=project["draft_source_sha256"],
    )
    inspection_payload = inspection.to_dict()
    installed_factor = next(
        item
        for item in inspection_payload["entrypoints"]
        if item["kind"] == "factor" and item["id"] not in existing_factor_ids
    )
    return {
        "project": updated_project,
        "inspection": inspection_payload,
        "factor": installed_factor,
        "template": get_factor_template(template_id).to_dict(),
    }


def get_entrypoint_source(project_id: str, entrypoint_id: str) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    entrypoint = next(
        (item for item in project["inspection"]["entrypoints"] if item["id"] == entrypoint_id),
        None,
    )
    if entrypoint is None:
        raise KeyError(entrypoint_id)
    if entrypoint["kind"] == "factor":
        repo = repository()
        try:
            source = repo.get_factor_source(project_id, entrypoint_id)["source"]
        finally:
            repo.close()
    else:
        source = registered_function_source(project["strategy_source"], entrypoint_id=entrypoint_id)
    return {
        "project_id": project_id,
        "entrypoint_id": entrypoint_id,
        "source_sha256": project["draft_source_sha256"],
        "source": source,
    }


def _apply_structured_edit(source: str, payload: Mapping[str, Any]) -> tuple[str, SourceInspection]:
    operation = str(payload.get("operation") or "")
    if operation == "parameter":
        return update_parameter_default(
            source,
            entrypoint_id=str(payload["entrypoint_id"]),
            parameter=str(payload["parameter"]),
            value=payload.get("value"),
        )
    if operation == "schedule":
        return update_signal_schedule(
            source,
            signal_id=str(payload["entrypoint_id"]),
            frequency=str(payload["frequency"]),
            selector=str(payload.get("selector") or "every"),
            at=str(payload["at"]),
        )
    if operation == "factor_blend":
        return update_signal_factor_blend(
            source,
            signal_id=str(payload["entrypoint_id"]),
            factor_weights=dict(payload["factor_weights"]),
            normalization=str(payload["normalization"]),
        )
    if operation == "replace_function":
        return replace_registered_function(
            source,
            entrypoint_id=str(payload["entrypoint_id"]),
            function_source=str(payload["function_source"]),
        )
    if operation == "delete_function":
        return delete_registered_function(
            source,
            entrypoint_id=str(payload["entrypoint_id"]),
        )
    raise ValueError("unsupported structured edit operation")


def preview_structured_edits(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    expected = payload.get("expected_source_sha256")
    if expected and expected != project["draft_source_sha256"]:
        raise RuntimeError("draft changed since it was inspected")
    updated = project["draft_source"]
    inspection = inspect_strategy_source(updated)
    for edit in payload.get("edits") or ():
        updated, inspection = _apply_structured_edit(updated, edit)
    strategy_source, _ = split_strategy_source(updated)
    return {
        "project_id": project_id,
        "base_source_sha256": project["draft_source_sha256"],
        "source": strategy_source,
        "source_sha256": inspection.source_sha256,
        "inspection": inspection.to_dict(),
    }


def structured_edit(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    expected = payload.get("expected_source_sha256")
    if expected and expected != project["draft_source_sha256"]:
        raise RuntimeError("draft changed since it was inspected")
    operation = str(payload.get("operation") or "")
    entrypoint_id = str(payload.get("entrypoint_id") or "")
    entrypoint = next(
        (item for item in project["inspection"]["entrypoints"] if item["id"] == entrypoint_id),
        None,
    )
    if operation in {"replace_function", "delete_function"} and entrypoint is None:
        raise KeyError(entrypoint_id)
    if operation == "replace_function" and entrypoint["kind"] == "factor":
        repo = repository()
        try:
            updated_project = repo.replace_factor_source(
                project_id,
                entrypoint_id,
                str(payload["function_source"]),
                expected_source_sha256=project["draft_source_sha256"],
            )
        finally:
            repo.close()
        return {
            "project": updated_project,
            "inspection": updated_project["inspection"],
        }
    if operation == "delete_function" and entrypoint["kind"] == "factor":
        repo = repository()
        try:
            updated_project = repo.delete_factor_source(
                project_id,
                entrypoint_id,
                expected_source_sha256=project["draft_source_sha256"],
            )
        finally:
            repo.close()
        return {
            "project": updated_project,
            "inspection": updated_project["inspection"],
        }

    source = project["draft_source"]
    if operation == "batch":
        updated = source
        inspection = inspect_strategy_source(source)
        for edit in payload.get("edits") or ():
            updated, inspection = _apply_structured_edit(updated, edit)
    else:
        updated, inspection = _apply_structured_edit(source, payload)
    project = update_draft(
        project_id,
        updated,
        expected_source_sha256=project["draft_source_sha256"],
    )
    return {
        "project": project,
        "inspection": inspection.to_dict(),
    }


def insertion(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    expected = payload.get("expected_source_sha256")
    if expected and expected != project["draft_source_sha256"]:
        raise RuntimeError("draft changed since it was inspected")
    kind = str(payload.get("kind") or "source")
    if kind == "field":
        snippet = factor_field_snippet(str(payload["value"]))
    elif kind == "factor":
        snippet = factor_dependency_snippet(
            str(payload["value"]), **dict(payload.get("parameters") or {})
        )
    elif kind == "source":
        snippet = str(payload["value"])
    else:
        raise ValueError("insertion kind must be field, factor, or source")
    updated, inspection = insert_source(
        project["draft_source"],
        cursor=int(payload["cursor"]),
        snippet=snippet,
    )
    if inspection is None:
        return {
            "project_id": project_id,
            "source": updated,
            "valid": False,
            "persisted": False,
            "snippet": snippet,
        }
    updated_project = update_draft(
        project_id,
        updated,
        expected_source_sha256=project["draft_source_sha256"],
    )
    return {
        "project": updated_project,
        "inspection": inspection.to_dict(),
        "valid": True,
        "persisted": True,
        "snippet": snippet,
    }


def preview_project(
    project_id: str,
    *,
    operation: str,
    profile: str,
    as_of_date: str | None,
    revision: int | None,
) -> dict[str, Any]:
    if profile != "runtime":
        raise ValueError("strategy previews use the runtime data profile")
    _, profile_end = _profile_range(profile)
    decision_date = as_of_date or profile_end
    if pd.Timestamp(decision_date) > pd.Timestamp(profile_end):
        decision_date = profile_end
    repo = repository()
    try:
        result = preview_strategy(
            repo,
            project_id,
            _engine(profile),
            decision_date,
            operation=operation,
            revision=revision,
        )
        return {**result, "profile": profile}
    finally:
        repo.close()


def factor_snapshot(
    project_id: str,
    factor_id: str,
    *,
    profile: str,
    as_of_date: str,
    revision: int | None,
    parameters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if profile != "runtime":
        raise ValueError("factor evaluations use the runtime data profile")
    repo = repository()
    try:
        return evaluate_factor_snapshot(
            repo,
            project_id,
            factor_id,
            _engine(profile),
            as_of_date,
            revision=revision,
            parameters=parameters,
        )
    finally:
        repo.close()


def factor_history(
    project_id: str,
    factor_id: str,
    *,
    profile: str,
    start_date: str,
    end_date: str,
    revision: int | None,
    parameters: Mapping[str, Any] | None,
    frequency: str,
) -> dict[str, Any]:
    if profile != "runtime":
        raise ValueError("factor evaluations use the runtime data profile")
    repo = repository()
    try:
        return evaluate_factor_history(
            repo,
            project_id,
            factor_id,
            _engine(profile),
            start_date,
            end_date,
            revision=revision,
            parameters=parameters,
            frequency=frequency,
        )
    finally:
        repo.close()


def preflight_project_backtest(
    project_id: str,
    start_date: str,
    end_date: str,
    profile: str,
    revision: int | None = None,
) -> dict[str, Any]:
    if profile != "runtime":
        raise ValueError("new backtests use the runtime data profile")
    repo = repository()
    try:
        return preflight_strategy_backtest(
            repo,
            project_id,
            start_date,
            end_date,
            _engine("runtime"),
            revision=revision,
        )
    finally:
        repo.close()


def run_project_backtest(
    project_id: str,
    start_date: str,
    end_date: str,
    profile: str,
    revision: int | None = None,
    validation_revision: int | None = None,
    backtest_id: str | None = None,
) -> dict[str, Any]:
    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
    repo = repository()
    try:
        validation_repo = ValidationRepository(repo.path)
        try:
            validation_package = validation_repo.get_package(project_id, validation_revision)
        finally:
            validation_repo.close()
        package = repo.get_package(project_id, revision)
        if package is None:
            raise KeyError(f"{project_id}@{revision}")
        pinned_revision = int(package["revision"])
        runtime_datasets = _runtime_datasets_for_backtest(package)
        engine = _engine(profile)
        with ExitStack() as data_locks:
            if profile == "runtime":
                runtime_store = RuntimeStore()
                for dataset in runtime_datasets:
                    data_locks.enter_context(
                        runtime_store.dataset_read_lock(dataset, timeout_seconds=30.0)
                    )
            provenance = build_research_provenance(
                profile,
                strategy_python=package["source"],
                runtime_datasets=runtime_datasets,
            )
            run = run_strategy_backtest(
                repo,
                project_id,
                start_date,
                end_date,
                engine,
                revision=pinned_revision,
                execution_data_policy="strict" if profile == "runtime" else "illustrative",
            )
            returns = run.returns
            weights = run.weights
            configured_benchmark = run.project["settings"].get("benchmark_symbols")
            symbols = list(configured_benchmark or run.benchmark_symbols)
            benchmark = (
                equal_weight_benchmark(
                    engine,
                    symbols,
                    start_date,
                    end_date,
                    frequency="daily",
                    execution_price="next_open",
                ).reindex(returns.index)
                if symbols
                else pd.Series(0.0, index=returns.index, name="benchmark")
            )
            try:
                attribution_factors = engine.get_factors(
                    [*FACTOR_NAMES, "rf"],
                    start_date,
                    end_date,
                    freq="1M",
                    strict=False,
                    use_cache=False,
                )
            except MissingDataError:
                attribution_factors = pd.DataFrame()
            validation_output = execute_validation(
                validation_package["source"],
                returns=returns,
                benchmark_returns=benchmark,
                weights=weights,
                factor_returns=attribution_factors,
                executions=run.executions,
                settings=dict(run.project["settings"]),
            )
    finally:
        repo.close()
    metrics = dict(validation_output["performance"])
    attribution = dict(validation_output["alpha_beta"])
    provenance["strategy_source_package"] = run.diagnostics["strategy_source_package"]
    provenance["validation_source_package"] = {
        "revision": validation_package["revision"],
        "source_sha256": validation_package["source_sha256"],
    }
    provenance["benchmark"] = {
        "kind": "equal_weight_universe",
        "symbols": symbols,
        "source": "project_settings" if configured_benchmark else "prepared_instrument_master",
        "frequency": "daily",
        "execution_price": "next_open",
    }
    store = ResultStore()
    try:
        store.ensure_backtest_subject(
            run.project["id"],
            f"sqlite:strategy_projects/{run.project['id']}",
            run.project["description"],
        )
        backtest_id = store.save_backtest(
            returns,
            metrics,
            strategy_id=run.project["id"],
            benchmark=benchmark,
            weights=weights,
            start_date=start_date,
            end_date=end_date,
            tags=[f"profile:{profile}", "strategy:sdk-v1"],
            provenance=provenance,
            executions=run.executions,
            events=[
                *list(run.diagnostics.get("events") or ()),
                *[
                    {"kind": "delisting_settlement", **item}
                    for item in run.diagnostics.get("delisting_settlements") or ()
                ],
            ],
            strategy_source=run.package["source"],
            settings=dict(run.project["settings"]),
            attribution=attribution,
            strategy_project_id=run.project["id"],
            strategy_revision=run.package["revision"],
            strategy_source_sha256=run.package["source_sha256"],
            strategy_manifest=run.package["manifest"],
            validation_source=validation_package["source"],
            validation_revision=validation_package["revision"],
            validation_source_sha256=validation_package["source_sha256"],
            validation_output=validation_output,
            backtest_id=backtest_id,
        )
    finally:
        store.close()
    return {
        "id": backtest_id,
        "project_id": run.project["id"],
        "strategy_id": run.project["id"],
        "strategy_type": "sdk_v1",
        "revision": run.package["revision"],
        "start_date": start_date,
        "end_date": end_date,
        "profile": profile,
        "source_sha256": run.package["source_sha256"],
        "validation_revision": validation_package["revision"],
        "validation_source_sha256": validation_package["source_sha256"],
        "metrics": metrics,
        "returns": [
            {"date": str(date)[:10], "value": float(value)} for date, value in returns.items()
        ],
        "weights_count": int((weights.abs() > 1e-12).sum().sum()) if not weights.empty else 0,
        "counts": {
            "return_rows": len(returns),
            "weight_rows": int((weights.abs() > 1e-12).sum().sum()) if not weights.empty else 0,
            "executions": len(run.executions),
            "events": len(run.diagnostics.get("events") or ())
            + len(run.diagnostics.get("delisting_settlements") or ()),
            "delisting_settlements": len(run.diagnostics.get("delisting_settlements") or ()),
        },
        "execution": run.diagnostics,
        "strategy_manifest": run.package["manifest"],
        "attribution": attribution,
        "validation_output": validation_output,
        "provenance": provenance,
    }


def _runtime_datasets_for_backtest(package: Mapping[str, Any]) -> tuple[str, ...]:
    requirements = package.get("data_requirements") or {}
    datasets = {
        "rq.instruments",
        "rq.bars",
        "rq.paused",
        "runtime.factor_returns",
    }
    bar_fields = set(requirements.get("bars") or ())
    if "is_st" in bar_fields:
        datasets.add("rq.is_st")
    if requirements.get("daily_factors"):
        datasets.add("rq.daily_factors")
    if requirements.get("index_components"):
        datasets.add("rq.index_components")
    if requirements.get("fundamentals"):
        datasets.add("canonical.fundamentals")
    return tuple(sorted(datasets))


__all__ = [
    "add_project_factor_source",
    "add_project_factor_template",
    "clone_project",
    "create_project",
    "delete_project",
    "factor_history",
    "factor_snapshot",
    "factor_template_catalog",
    "get_entrypoint_source",
    "preview_structured_edits",
    "get_project",
    "get_revision",
    "insertion",
    "list_projects",
    "list_revisions",
    "preview_project",
    "preflight_project_backtest",
    "run_project_backtest",
    "save_revision",
    "structured_edit",
    "update_draft",
    "update_strategy_source",
    "update_metadata",
    "validate_source",
]
