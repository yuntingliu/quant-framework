"""Application service for Python-native pipeline projects."""
from __future__ import annotations

from typing import Any

import pandas as pd

from alphalab.analytics import FACTOR_NAMES, PerformanceMetrics, equal_weight_benchmark, factor_attribution
from alphalab.dataio import MissingDataError
from alphalab.pipeline import PipelineRepository, preview_pipeline_project, run_pipeline_project_backtest
from alphalab.provenance import build_research_provenance
from alphalab.store import ResultStore
from dashboard.backend.services.data_service import _engine, _profile_range


def repository() -> PipelineRepository:
    return PipelineRepository()


def list_components(stage: str | None = None) -> list[dict[str, Any]]:
    repo = repository()
    try:
        return repo.list_components(stage)
    finally:
        repo.close()


def get_component(component_id: str, version: int | None = None) -> dict[str, Any] | None:
    repo = repository()
    try:
        return repo.get_component(component_id, version)
    finally:
        repo.close()


def create_component(payload: dict[str, Any]) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.create_component(**payload)
    finally:
        repo.close()


def clone_component(component_id: str, target_id: str, name: str | None) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.clone_component(component_id, target_id, name)
    finally:
        repo.close()


def save_component_version(component_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.save_component_version(component_id, **payload)
    finally:
        repo.close()


def delete_component(component_id: str) -> bool:
    repo = repository()
    try:
        return repo.delete_component(component_id)
    finally:
        repo.close()


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


def create_project(payload: dict[str, Any]) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.create_project(**payload)
    finally:
        repo.close()


def clone_project(project_id: str, target_id: str, name: str | None) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.clone_project(project_id, target_id, name)
    finally:
        repo.close()


def update_project(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.update_project(project_id, **payload)
    finally:
        repo.close()


def delete_project(project_id: str) -> bool:
    repo = repository()
    try:
        return repo.delete_project(project_id)
    finally:
        repo.close()


def preview_project(
    project_id: str,
    *,
    stage: str,
    profile: str,
    as_of_date: str | None,
) -> dict[str, Any]:
    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
    _, profile_end = _profile_range(profile)
    decision_date = as_of_date or profile_end
    if pd.Timestamp(decision_date) > pd.Timestamp(profile_end):
        decision_date = profile_end
    repo = repository()
    try:
        result = preview_pipeline_project(
            repo,
            project_id,
            _engine(profile),
            decision_date,
            stage=stage,
        )
        return {**result, "profile": profile}
    finally:
        repo.close()


def run_project_backtest(
    project_id: str,
    start_date: str,
    end_date: str,
    profile: str,
) -> dict[str, Any]:
    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
    repo = repository()
    try:
        pipeline = run_pipeline_project_backtest(
            repo, project_id, start_date, end_date, _engine(profile)
        )
    finally:
        repo.close()
    returns = pipeline.result.returns
    weights = pipeline.result.weights
    universe = pipeline.config.universe
    engine = _engine(profile)
    symbols = list(universe.symbols) or engine.get_symbols(universe.pool)
    benchmark = equal_weight_benchmark(
        engine,
        symbols,
        start_date,
        end_date,
        frequency=pipeline.config.execution.rebalance_freq,
        execution_price=pipeline.config.execution.execution_price,
    ).reindex(returns.index)
    periods_per_year = (
        252
        if pipeline.config.execution.rebalance_freq == "daily"
        else 52
        if pipeline.config.execution.rebalance_freq == "weekly"
        else 12
    )
    metrics = PerformanceMetrics.summarize(returns, periods_per_year)
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
        executions=pipeline.result.executions,
        research_thresholds=dict(
            pipeline.project["settings"].get("research_thresholds") or {}
        ),
    )
    provenance = build_research_provenance(
        profile,
        strategy_python=pipeline.composed.source,
    )
    store = ResultStore()
    try:
        # This registration only satisfies the foreign key on databases created
        # before pipeline projects existed.  No YAML file is written or read.
        store.ensure_backtest_subject(
            pipeline.project["id"],
            f"sqlite:pipeline_projects/{pipeline.project['id']}",
            pipeline.project["description"],
        )
        backtest_id = store.save_backtest(
            returns,
            metrics,
            strategy_id=pipeline.project["id"],
            benchmark=benchmark,
            weights=weights,
            start_date=start_date,
            end_date=end_date,
            tags=[f"profile:{profile}", "strategy:python-pipeline"],
            provenance=provenance,
            executions=pipeline.result.executions,
            pipeline_project_id=pipeline.project["id"],
            strategy_source=pipeline.composed.source,
            component_manifest=list(pipeline.composed.manifest),
            settings=dict(pipeline.project["settings"]),
            attribution=attribution,
        )
    finally:
        store.close()
    return {
        "id": backtest_id,
        "project_id": pipeline.project["id"],
        "strategy_id": pipeline.project["id"],
        "strategy_type": "python_pipeline",
        "revision": pipeline.project["revision"],
        "source_sha256": pipeline.composed.source_sha256,
        "metrics": metrics,
        "returns": [
            {"date": str(date)[:10], "value": float(value)}
            for date, value in returns.items()
        ],
        "weights_count": int(weights.notna().sum().sum()) if not weights.empty else 0,
        "execution": pipeline.result.diagnostics,
        "component_manifest": list(pipeline.composed.manifest),
        "attribution": attribution,
        "provenance": provenance,
    }


__all__ = [
    "clone_component",
    "clone_project",
    "create_component",
    "create_project",
    "delete_component",
    "delete_project",
    "get_component",
    "get_project",
    "list_components",
    "list_projects",
    "preview_project",
    "run_project_backtest",
    "save_component_version",
    "update_project",
]
