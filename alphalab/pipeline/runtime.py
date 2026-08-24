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
    if isinstance(selection.get("selection"), dict):
        selection = {**selection["selection"], **{key: value for key, value in selection.items() if key != "selection"}}
    portfolio = parameters.get("portfolio", {})
    # Historical six-stage BacktestRuns keep their frozen risk parameters. This
    # read-only adaptation never makes the removed stage available to authoring.
    legacy_risk = parameters.get("risk", {})
    execution = parameters.get("execution", {})
    factors = list(settings.get("factors") or [])
    configured_weights = selection.get("factor_weights")
    if not isinstance(configured_weights, dict):
        configured_weights = {
            str(item.get("name")): float(item.get("weight", 1.0))
            for item in factors
            if isinstance(item, dict) and item.get("name")
        }
    universe = dict(settings.get("universe") or {})
    universe.setdefault("pool", "all")
    universe.setdefault("symbols", [])
    return StrategyConfig.from_dict(
        {
            "name": project["id"],
            "description": project.get("description", ""),
            "universe": universe,
            "factors": factors,
            "selection": {
                "min_factor_coverage": float(selection.get("min_factor_coverage", 0.0)),
                "n_stocks": int(selection.get("count", max(1, len(universe.get("symbols") or [])) or 20)),
                "signal_frequency": str(
                    selection.get("signal_frequency", execution.get("rebalance_freq", "monthly"))
                ),
                "normalization": str(selection.get("normalization", "percentile_rank")),
                "factor_weights": configured_weights,
                "exit_rank": int(
                    selection.get(
                        "exit_rank",
                        selection.get("count", max(1, len(universe.get("symbols") or [])) or 20),
                    )
                ),
            },
            "portfolio": {
                "max_weight": float(portfolio.get("max_weight", legacy_risk.get("max_weight", 1.0))),
                "max_gross_exposure": float(
                    portfolio.get(
                        "max_gross_exposure",
                        legacy_risk.get("max_gross_exposure", 1.0),
                    )
                ),
                "optimizer": str(portfolio.get("optimizer", "equal_weight")),
                "rank_decay": float(portfolio.get("rank_decay", 1.0)),
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
        strategy_source=project["composed_source"],
        lookback_days=int(project["settings"].get("lookback_days", 120)),
        pipeline_stage=stage,
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
        "targets": targets if stage in {"portfolio", "execution"} else {},
        "diagnostics": engine.diagnostics,
        "selection": engine.selection_snapshot,
        "stage_outputs": {
            reached_stage: dict(reached.get(reached_stage) or {})
            for reached_stage in STAGE_NAMES[: stage_index + 1]
        },
    }


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
        strategy_source=composed.source,
        data_engine=data_engine,
        lookback_days=int(project["settings"].get("lookback_days", 120)),
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
    "preview_pipeline_project",
    "project_strategy_config",
    "run_pipeline_project_backtest",
]
