"""Application service for canonical Strategy SDK v1 projects and runs."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from alphalab.analytics import (
    FACTOR_NAMES,
    PerformanceMetrics,
    equal_weight_benchmark,
    factor_attribution,
)
from alphalab.dataio import MissingDataError
from alphalab.provenance import build_research_provenance
from alphalab.store import ResultStore
from alphalab.strategy.engine import (
    evaluate_factor_history,
    evaluate_factor_snapshot,
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
    delete_registered_function,
    factor_dependency_snippet,
    factor_field_snippet,
    insert_source,
    inspect_strategy_source,
    registered_function_source,
    replace_registered_function,
    update_parameter_default,
    update_signal_factor_blend,
    update_signal_schedule,
)
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
        return repo.create_project(**dict(payload))
    finally:
        repo.close()


def clone_project(project_id: str, target_id: str, name: str | None) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.clone_project(project_id, target_id, name=name)
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
    updated, inspection = install_factor_template(project["draft_source"], template_id=template_id)
    updated_project = update_draft(
        project_id,
        updated,
        expected_source_sha256=project["draft_source_sha256"],
    )
    return {
        "project": updated_project,
        "inspection": inspection.to_dict(),
        "template": get_factor_template(template_id).to_dict(),
    }


def get_entrypoint_source(project_id: str, entrypoint_id: str) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    return {
        "project_id": project_id,
        "entrypoint_id": entrypoint_id,
        "source_sha256": project["draft_source_sha256"],
        "source": registered_function_source(project["draft_source"], entrypoint_id=entrypoint_id),
    }


def structured_edit(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    source = project["draft_source"]
    expected = payload.get("expected_source_sha256")
    if expected and expected != project["draft_source_sha256"]:
        raise RuntimeError("draft changed since it was inspected")
    operation = str(payload.get("operation") or "")
    if operation == "parameter":
        updated, inspection = update_parameter_default(
            source,
            entrypoint_id=str(payload["entrypoint_id"]),
            parameter=str(payload["parameter"]),
            value=payload.get("value"),
        )
    elif operation == "schedule":
        updated, inspection = update_signal_schedule(
            source,
            signal_id=str(payload["entrypoint_id"]),
            frequency=str(payload["frequency"]),
            selector=str(payload.get("selector") or "every"),
            at=str(payload["at"]),
        )
    elif operation == "factor_blend":
        updated, inspection = update_signal_factor_blend(
            source,
            signal_id=str(payload["entrypoint_id"]),
            factor_weights=dict(payload["factor_weights"]),
            normalization=str(payload["normalization"]),
        )
    elif operation == "replace_function":
        updated, inspection = replace_registered_function(
            source,
            entrypoint_id=str(payload["entrypoint_id"]),
            function_source=str(payload["function_source"]),
        )
    elif operation == "delete_function":
        updated, inspection = delete_registered_function(
            source,
            entrypoint_id=str(payload["entrypoint_id"]),
        )
    else:
        raise ValueError("unsupported structured edit operation")
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
    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
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


def run_project_backtest(
    project_id: str,
    start_date: str,
    end_date: str,
    profile: str,
    revision: int | None = None,
) -> dict[str, Any]:
    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
    repo = repository()
    try:
        run = run_strategy_backtest(
            repo,
            project_id,
            start_date,
            end_date,
            _engine(profile),
            revision=revision,
        )
    finally:
        repo.close()
    returns = run.returns
    weights = run.weights
    engine = _engine(profile)
    symbols = list(weights.columns)
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
    metrics = PerformanceMetrics.summarize(returns, 252)
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
    attribution = factor_attribution(
        returns,
        attribution_factors,
        executions=run.executions,
        research_thresholds=dict(run.project["settings"].get("research_thresholds") or {}),
    )
    provenance = build_research_provenance(
        profile,
        strategy_python=run.package["source"],
    )
    provenance["strategy_source_package"] = run.diagnostics["strategy_source_package"]
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
            strategy_source=run.package["source"],
            settings=dict(run.project["settings"]),
            attribution=attribution,
            strategy_project_id=run.project["id"],
            strategy_revision=run.package["revision"],
            strategy_source_sha256=run.package["source_sha256"],
            strategy_manifest=run.package["manifest"],
        )
    finally:
        store.close()
    return {
        "id": backtest_id,
        "project_id": run.project["id"],
        "strategy_id": run.project["id"],
        "strategy_type": "sdk_v1",
        "revision": run.package["revision"],
        "source_sha256": run.package["source_sha256"],
        "metrics": metrics,
        "returns": [
            {"date": str(date)[:10], "value": float(value)} for date, value in returns.items()
        ],
        "weights_count": int((weights.abs() > 1e-12).sum().sum()) if not weights.empty else 0,
        "execution": run.diagnostics,
        "strategy_manifest": run.package["manifest"],
        "attribution": attribution,
        "provenance": provenance,
    }


__all__ = [
    "add_project_factor_template",
    "clone_project",
    "create_project",
    "delete_project",
    "factor_history",
    "factor_snapshot",
    "factor_template_catalog",
    "get_entrypoint_source",
    "get_project",
    "get_revision",
    "insertion",
    "list_projects",
    "list_revisions",
    "preview_project",
    "run_project_backtest",
    "save_revision",
    "structured_edit",
    "update_draft",
    "update_metadata",
    "validate_source",
]
