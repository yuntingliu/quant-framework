"""Application service for Python-native pipeline projects."""
from __future__ import annotations

from typing import Any

import pandas as pd

from alphalab.analytics import PerformanceMetrics, equal_weight_benchmark
from alphalab.pipeline import PipelineRepository, preview_pipeline_project, run_pipeline_project_backtest
from alphalab.pipeline.models import STAGE_NAMES
from alphalab.pipeline.runtime import analyze_pipeline_project_stage
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
        return preview_pipeline_project(
            repo,
            project_id,
            _engine(profile),
            decision_date,
            stage=stage,
        )
    finally:
        repo.close()


def analyze_project(
    project_id: str,
    *,
    stage: str,
    profile: str,
    months: int,
) -> dict[str, Any]:
    """Run the frozen project over a bounded window and expose stage traces.

    This is intentionally a read-only analysis path.  It uses the same complete
    Python source and guarded backtest core as a saved backtest, but does not
    create a ResultStore record.
    """

    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
    if not 3 <= months <= 60:
        raise ValueError("months must be between 3 and 60")
    profile_start, profile_end = _profile_range(profile)
    end = pd.Timestamp(profile_end)
    start = max(pd.Timestamp(profile_start), end - pd.DateOffset(months=months))
    if start >= end:
        raise ValueError("profile does not contain enough history for stage analysis")
    repo = repository()
    try:
        pipeline = analyze_pipeline_project_stage(
            repo,
            project_id,
            stage,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
            _engine(profile),
        )
    finally:
        repo.close()
    points = []
    for execution in pipeline.result.executions:
        points.append(
            {
                "signal_date": execution.get("signal_date"),
                "entry_date": execution.get("entry_date"),
                "exit_date": execution.get("exit_date"),
                "stage_outputs": dict(execution.get("stage_outputs") or {}),
                "turnover": float(execution.get("turnover", 0.0)),
                "total_cost": float(execution.get("total_cost", 0.0)),
                "cash_weight": float(execution.get("cash_weight", 0.0)),
                "restrictions": list(execution.get("restrictions") or []),
            }
        )
    return {
        "project_id": project_id,
        "revision": pipeline.project["revision"],
        "source_sha256": pipeline.composed.source_sha256,
        "profile": profile,
        "requested_stage": stage,
        "executed_stages": list(STAGE_NAMES[: STAGE_NAMES.index(stage) + 1]),
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "points": points,
    }


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
        frequency=pipeline.config.portfolio.rebalance_freq,
        execution_price=pipeline.config.execution.execution_price,
    ).reindex(returns.index)
    periods_per_year = 52 if pipeline.config.portfolio.rebalance_freq == "weekly" else 12
    metrics = PerformanceMetrics.summarize(returns, periods_per_year)
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
        "provenance": provenance,
    }


__all__ = [
    "analyze_project",
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
