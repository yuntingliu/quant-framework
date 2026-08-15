"""Adapter from a versioned pipeline project to the guarded backtest core."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alphalab.dataio import DataEngine
from alphalab.engine import BacktestResult, SignalEngine, run_backtest_detailed
from alphalab.pipeline.compose import ComposedStrategy
from alphalab.pipeline.models import STAGE_NAMES
from alphalab.pipeline.repository import PipelineRepository
from alphalab.strategy.config import StrategyConfig


@dataclass(frozen=True)
class PipelineBacktestResult:
    project: dict[str, Any]
    config: StrategyConfig
    composed: ComposedStrategy
    result: BacktestResult


def project_strategy_config(project: dict[str, Any]) -> StrategyConfig:
    settings = dict(project.get("settings") or {})
    parameters = {
        item["stage"]: dict(item.get("parameters") or {})
        for item in project.get("component_manifest") or []
    }
    selection = parameters.get("selection", {})
    risk = parameters.get("risk", {})
    execution = parameters.get("execution", {})
    universe = dict(settings.get("universe") or {})
    universe.setdefault("pool", "all")
    universe.setdefault("symbols", [])
    return StrategyConfig.from_dict(
        {
            "strategy_type": "stock_selection",
            "name": project["id"],
            "description": project.get("description", ""),
            "universe": universe,
            "factors": list(settings.get("factors") or []),
            "selection": {
                "min_factor_coverage": float(selection.get("min_factor_coverage", 0.0)),
                "n_stocks": int(selection.get("count", max(1, len(universe.get("symbols") or [])) or 20)),
            },
            "portfolio": {
                "max_weight": float(risk.get("max_weight", 1.0)),
                "rebalance_freq": str(execution.get("rebalance_freq", "monthly")),
                "optimizer": "equal_weight",
            },
            "execution": {
                "cost_bps": float(execution.get("cost_bps", 20.0)),
                "slippage_bps": float(execution.get("slippage_bps", 0.0)),
                "impact_bps": float(execution.get("impact_bps", 0.0)),
                "execution_price": str(execution.get("execution_price", "next_open")),
                "portfolio_value": float(execution.get("portfolio_value", 1_000_000.0)),
                "max_participation_rate": float(execution.get("max_participation_rate", 0.1)),
            },
            "metadata": {
                "pipeline_project_revision": project["revision"],
                "pipeline_source_sha256": project["source_sha256"],
            },
        }
    )


def preview_pipeline_project(
    repository: PipelineRepository,
    project_id: str,
    data_engine: DataEngine,
    as_of_date: str,
    *,
    stage: str,
) -> dict[str, Any]:
    if stage not in STAGE_NAMES:
        raise ValueError(f"stage must be one of {STAGE_NAMES}")
    project = repository.get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    config = project_strategy_config(project)
    stage_parameters = {
        item["stage"]: dict(item["parameters"])
        for item in project["component_manifest"]
    }
    engine = SignalEngine(data_engine)
    targets = engine.generate_targets(
        config,
        as_of_date,
        lookback_days=int(project["settings"].get("lookback_days", 120)),
        complete_python_source=project["composed_source"],
        complete_pipeline_stage=stage,
        stage_parameters=stage_parameters,
    )
    reached = engine.diagnostics.get("complete_pipeline", {})
    stage_index = STAGE_NAMES.index(stage)
    return {
        "project_id": project_id,
        "revision": project["revision"],
        "source_sha256": project["source_sha256"],
        "signal_date": engine.diagnostics.get("as_of_date", as_of_date),
        "requested_stage": stage,
        "executed_stages": list(STAGE_NAMES[: stage_index + 1]),
        "targets": targets if stage in {"risk", "execution"} else {},
        "diagnostics": engine.diagnostics,
        "selection": engine.selection_snapshot,
        "stage_outputs": {
            reached_stage: dict(reached.get(reached_stage) or {})
            for reached_stage in STAGE_NAMES[: stage_index + 1]
        },
    }


def analyze_pipeline_project_stage(
    repository: PipelineRepository,
    project_id: str,
    stage: str,
    start_date: str,
    end_date: str,
    data_engine: DataEngine,
) -> PipelineBacktestResult:
    """Run only a stage and its upstream dependencies over rebalance history."""

    if stage not in STAGE_NAMES:
        raise ValueError(f"stage must be one of {STAGE_NAMES}")
    project = repository.get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    config = project_strategy_config(project)
    composed = repository.compose(project_id)
    stage_parameters = {
        item["stage"]: dict(item["parameters"])
        for item in project["component_manifest"]
    }
    result = run_backtest_detailed(
        config,
        start_date,
        end_date,
        data_engine=data_engine,
        lookback_days=int(project["settings"].get("lookback_days", 120)),
        complete_python_source=composed.source,
        complete_pipeline_stage=stage,
        stage_parameters=stage_parameters,
    )
    return PipelineBacktestResult(
        project=project,
        config=config,
        composed=composed,
        result=result,
    )


def run_pipeline_project_backtest(
    repository: PipelineRepository,
    project_id: str,
    start_date: str,
    end_date: str,
    data_engine: DataEngine,
) -> PipelineBacktestResult:
    project = repository.get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    config = project_strategy_config(project)
    composed = repository.compose(project_id)
    stage_parameters = {
        item["stage"]: dict(item["parameters"])
        for item in project["component_manifest"]
    }
    result = run_backtest_detailed(
        config,
        start_date,
        end_date,
        data_engine=data_engine,
        lookback_days=int(project["settings"].get("lookback_days", 120)),
        complete_python_source=composed.source,
        stage_parameters=stage_parameters,
    )
    return PipelineBacktestResult(
        project=project,
        config=config,
        composed=composed,
        result=result,
    )


__all__ = [
    "PipelineBacktestResult",
    "analyze_pipeline_project_stage",
    "preview_pipeline_project",
    "project_strategy_config",
    "run_pipeline_project_backtest",
]
