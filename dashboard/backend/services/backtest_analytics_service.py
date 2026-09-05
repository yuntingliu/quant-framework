"""Derived analytics for persisted barebone backtests."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from alphalab.analytics import RobustnessThresholds, equal_weight_benchmark, robustness_report
from alphalab.strategy.config import StrategyConfig
from alphalab.strategy.source import SourceInspection, inspect_strategy_source
from dashboard.backend.services.data_service import _engine
from dashboard.backend.services.legacy_backtest_adapter import legacy_config, legacy_snapshot
from dashboard.backend.services.result_service import get_backtest


def _safe(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    numeric = float(value)
    if not np.isfinite(numeric):
        return None
    return int(numeric) if isinstance(value, int) else numeric


def _return_periods_per_year(index: pd.Index) -> int:
    """Infer the observation cadence from the frozen return dates, not rebalance timing."""

    dates = pd.DatetimeIndex(pd.to_datetime(index)).drop_duplicates().sort_values()
    if len(dates) < 2:
        return 252
    median_days = float(pd.Series(dates[1:] - dates[:-1]).dt.total_seconds().median()) / 86_400.0
    if median_days <= 3.0:
        return 252
    if median_days <= 10.0:
        return 52
    if median_days <= 45.0:
        return 12
    if median_days <= 120.0:
        return 4
    return 1


def _frozen_entrypoint_parameters(
    manifest: list[dict[str, Any]],
    inspection: SourceInspection,
    kind: str,
) -> dict[str, Any]:
    frozen = next(
        (
            item.get("parameters")
            for item in manifest
            if item.get("kind") == kind and isinstance(item.get("parameters"), dict)
        ),
        None,
    )
    if frozen is not None:
        return dict(frozen)
    entrypoint = next(
        (item for item in inspection.entrypoints if item.kind == kind),
        None,
    )
    return {
        item.name: item.default
        for item in (entrypoint.parameters if entrypoint else ())
        if item.editable
    }


def _compound_with_gaps(values: pd.Series) -> list[float | None]:
    cumulative = 1.0
    result: list[float | None] = []
    for value in values.tolist():
        if pd.isna(value):
            result.append(None)
            continue
        cumulative *= 1.0 + float(value)
        result.append(_safe(cumulative))
    return result


def _drawdown_from_curve(values: list[float | None]) -> list[float | None]:
    peak: float | None = None
    result: list[float | None] = []
    for value in values:
        if value is None:
            result.append(None)
            continue
        peak = value if peak is None else max(peak, value)
        result.append(_safe(value / peak - 1.0))
    return result


def _benchmark_returns(
    record: dict,
    returns: pd.Series,
    *,
    recompute: bool = False,
) -> pd.Series:
    return_rows = sorted(record.get("returns", []), key=lambda item: item["date"])
    benchmark = pd.Series(
        [
            float(item["benchmark"]) if item.get("benchmark") not in {None, ""} else np.nan
            for item in return_rows
        ],
        index=returns.index,
        dtype=float,
    )
    if not recompute or benchmark.empty or benchmark.notna().mean() >= 0.80:
        return benchmark
    try:
        config = _config_from_record(record)
    except ValueError:
        return benchmark
    engine = _engine(record.get("profile") or "demo")
    if config.metadata.get("legacy_strategy_type") == "market_timing":
        market_factor = str(config.metadata.get("legacy_market_factor") or "MKT")
        return engine.get_factors(
            [market_factor],
            record["start_date"],
            record["end_date"],
            freq="1M",
            strict=True,
            use_cache=False,
        )[market_factor].reindex(returns.index)
    return equal_weight_benchmark(
        engine,
        list(config.universe.symbols) or engine.get_symbols(config.universe.pool),
        record["start_date"],
        record["end_date"],
        frequency=config.selection.signal_frequency,
        execution_price=config.execution.execution_price,
    ).reindex(returns.index)


def analyze_record(record: dict) -> dict:
    return_rows = sorted(record.get("returns", []), key=lambda item: item["date"])
    dates = [str(item["date"]) for item in return_rows]
    returns = pd.Series(
        [float(item.get("value") or 0.0) for item in return_rows],
        index=pd.to_datetime(dates),
        dtype=float,
    )
    equity = (1.0 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0 if not equity.empty else equity
    benchmark_returns = _benchmark_returns(record, returns)
    excess_returns = returns - benchmark_returns
    benchmark_equity = _compound_with_gaps(benchmark_returns)
    excess_equity = _compound_with_gaps(excess_returns)

    weight_rows = record.get("weights", [])
    if weight_rows:
        weights = pd.DataFrame(weight_rows)
        weights["date"] = pd.to_datetime(weights["date"])
        pivot = (
            weights.pivot_table(
                index="date",
                columns="symbol",
                values="weight",
                aggfunc="last",
                fill_value=0.0,
            )
            .sort_index()
            .astype(float)
        )
    else:
        pivot = pd.DataFrame(dtype=float)

    snapshots = []
    turnover = []
    execution_turnover = {
        str(item.get("entry_date")): float(item.get("turnover") or 0.0)
        for item in record.get("executions", [])
        if isinstance(item, dict) and item.get("entry_date")
    }
    previous = pd.Series(dtype=float)
    for date, row in pivot.iterrows():
        current = row.loc[row.abs() > 1e-12]
        symbols = previous.index.union(row.index)
        changed = row.reindex(symbols, fill_value=0.0) - previous.reindex(symbols, fill_value=0.0)
        cash = 1.0 - float(row.sum())
        previous_cash = 1.0 - float(previous.sum())
        calculated_turnover = float((changed.abs().sum() + abs(cash - previous_cash)) / 2.0)
        date_text = date.strftime("%Y-%m-%d")
        period_turnover = execution_turnover.get(date_text, calculated_turnover)
        turnover.append({"date": date_text, "value": period_turnover})
        ordered = current.sort_values(ascending=False)
        snapshots.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "holdings_count": int(len(current)),
                "gross_exposure": float(current.abs().sum()),
                "concentration": float((current**2).sum()),
                "max_weight": float(current.max()) if not current.empty else 0.0,
                "top_holdings": [
                    {"symbol": str(symbol), "weight": float(weight)}
                    for symbol, weight in ordered.head(10).items()
                ],
            }
        )
        previous = row

    metrics = {key: _safe(value) for key, value in (record.get("metrics") or {}).items()}
    strategy_snapshot = None
    strategy_source = record.get("strategy_source")
    if isinstance(strategy_source, str) and strategy_source.strip():
        sdk_manifest = record.get("strategy_manifest")
        if isinstance(sdk_manifest, list) and sdk_manifest:
            inspection = inspect_strategy_source(strategy_source)
            signal = next((item for item in sdk_manifest if item.get("kind") == "signal"), {})
            execution_parameters = _frozen_entrypoint_parameters(
                sdk_manifest, inspection, "execution"
            )
            portfolio_parameters = _frozen_entrypoint_parameters(
                sdk_manifest, inspection, "portfolio"
            )
            strategy_snapshot = {
                "strategy_type": "sdk_v1",
                "implementation": "python",
                "project_id": record.get("strategy_project_id") or record.get("strategy_id"),
                "revision": record.get("strategy_revision"),
                "source_sha256": record.get("strategy_source_sha256"),
                "sdk_version": inspection.sdk_version,
                "validator_version": inspection.validator_version,
                "entrypoints": sdk_manifest,
                "source": strategy_source,
                "name": record.get("strategy_project_id") or record.get("strategy_id") or "sdk-v1",
                "description": "Immutable AlphaLab Strategy SDK v1 source package",
                "factors": [
                    str(item.get("id")) for item in sdk_manifest if item.get("kind") == "factor"
                ],
                "signals": [str(signal.get("id"))] if signal.get("id") else [],
                "rebalance_freq": (
                    ((signal.get("metadata") or {}).get("schedule") or {}).get("frequency")
                    or "custom"
                ),
                "execution_price": (
                    "next_open"
                    if execution_parameters.get("activation", "next_session_open")
                    == "next_session_open"
                    else "next_close"
                ),
                "cost_bps": float(execution_parameters.get("commission_rate", 0.0) or 0.0)
                * 10_000.0,
                "max_weight": float(
                    portfolio_parameters.get(
                        "max_weight", (record.get("settings") or {}).get("max_weight", 1.0)
                    )
                ),
            }
        else:
            strategy_snapshot = _persisted_strategy_snapshot(record, strategy_source)
    else:
        config_yaml = record.get("config_yaml")
        if isinstance(config_yaml, str) and config_yaml.strip():
            strategy_snapshot = legacy_snapshot(config_yaml)
    executions = record.get("executions", [])
    return {
        "id": record["id"],
        "strategy_id": record.get("strategy_id") or "unknown",
        "profile": record.get("profile") or "demo",
        "start_date": record.get("start_date"),
        "end_date": record.get("end_date"),
        "run_at": record.get("run_at"),
        "metrics": metrics,
        "dates": dates,
        "returns": [_safe(value) for value in returns.tolist()],
        "equity_curve": [_safe(value) for value in equity.tolist()],
        "drawdown": [_safe(value) for value in drawdown.tolist()],
        "benchmark_returns": [_safe(value) for value in benchmark_returns.tolist()],
        "benchmark_equity_curve": benchmark_equity,
        "benchmark_drawdown": _drawdown_from_curve(benchmark_equity),
        "excess_returns": [_safe(value) for value in excess_returns.tolist()],
        "excess_equity_curve": excess_equity,
        "benchmark_coverage": (
            _safe(benchmark_returns.notna().mean()) if not benchmark_returns.empty else None
        ),
        "turnover": turnover,
        "average_turnover": (
            _safe(np.mean([item["value"] for item in turnover])) if turnover else None
        ),
        "holdings": snapshots,
        "executions": executions,
        "has_execution_audit": bool(executions),
        "strategy_snapshot": strategy_snapshot,
        "provenance": record.get("provenance", {}),
    }


def _persisted_strategy_snapshot(record: dict, strategy_source: str) -> dict:
    settings = record.get("settings") if isinstance(record.get("settings"), dict) else {}
    manifest = (
        record.get("component_manifest")
        if isinstance(record.get("component_manifest"), list)
        else []
    )
    selection_parameters = next(
        (item.get("parameters", {}) for item in manifest if item.get("stage") == "selection"), {}
    )
    execution = next(
        (item.get("parameters", {}) for item in manifest if item.get("stage") == "execution"), {}
    )
    portfolio_parameters = next(
        (item.get("parameters", {}) for item in manifest if item.get("stage") == "portfolio"), {}
    )
    legacy_risk = next(
        (item.get("parameters", {}) for item in manifest if item.get("stage") == "risk"), {}
    )
    factors = [str(item.get("name")) for item in settings.get("factors", []) if item.get("name")]
    contracts = {
        "universe": (
            "point-in-time eligible candidates",
            "{'symbols': [...]}",
            "symbols remain inside the eligible base pool",
        ),
        "selection": (
            "universe and signal features",
            "{'selected': [...], 'scores': {...}}",
            "selected symbols remain inside the stage universe",
        ),
        "timing": (
            "historical point-in-time market history",
            "{'exposure': number}",
            "read-only historical contract",
        ),
        "portfolio": (
            "selection output and portfolio limits",
            "{'weights': {...}}",
            "core rechecks membership, concentration and gross exposure",
        ),
        "risk": (
            "historical proposed portfolio and limits",
            "{'weights': {...}}",
            "read-only historical contract",
        ),
        "execution": (
            "target/current weights and assumptions",
            "{'execution': {...}}",
            "core applies prices, liquidity, cash and costs",
        ),
    }
    snapshot_stage_names = [
        str(item.get("stage")) for item in manifest if str(item.get("stage")) in contracts
    ]
    stages = []
    for order, stage in enumerate(snapshot_stage_names, start=1):
        item = next((entry for entry in manifest if entry.get("stage") == stage), {})
        contract = contracts[stage]
        stages.append(
            {
                "order": order,
                "name": stage,
                "kind": "python",
                "entrypoint": item.get("entrypoint"),
                "runtime_function": item.get("entrypoint"),
                "timeout_seconds": 15.0,
                "contract": {"input": contract[0], "output": contract[1], "hard_gate": contract[2]},
                "source": f"# {item.get('component_id', stage)}@{item.get('version', '?')}\n# See the frozen module below.",
            }
        )
    return {
        "strategy_type": "python_pipeline",
        "implementation": "python",
        "python_stages": snapshot_stage_names,
        "pipeline": {},
        "pipeline_manifest": {
            "strategy_type": "python_pipeline",
            "order": snapshot_stage_names,
            "python_stages": snapshot_stage_names,
            "stages": stages,
            "composed_source": strategy_source,
            "invariants": [
                "all inputs are bounded by the decision date",
                "core validation cannot be bypassed by component code",
                "signals take effect on the next observed session",
            ],
        },
        "name": record.get("pipeline_project_id") or record.get("strategy_id") or "pipeline",
        "description": f"Version-pinned {len(snapshot_stage_names)}-stage Python strategy snapshot",
        "factors": factors,
        "signals": [],
        "rebalance_freq": selection_parameters.get(
            "signal_frequency", execution.get("rebalance_freq", "monthly")
        ),
        "execution_price": execution.get("execution_price", "next_open"),
        "cost_bps": execution.get("cost_bps", 0.0),
        "max_weight": portfolio_parameters.get("max_weight", legacy_risk.get("max_weight", 1.0)),
        "component_manifest": manifest,
    }


def analyze_backtest(backtest_id: str) -> dict:
    record = get_backtest(backtest_id)
    if record is None:
        raise KeyError(backtest_id)
    return analyze_record(record)


def analyze_attribution(backtest_id: str) -> dict:
    """Return the factor snapshot frozen when the backtest was persisted."""

    record = get_backtest(backtest_id)
    if record is None:
        raise KeyError(backtest_id)
    attribution = record.get("attribution")
    if not isinstance(attribution, dict) or not attribution:
        empty_regression = {
            "observations": 0,
            "alpha_monthly": None,
            "alpha_annualized": None,
            "betas": {},
            "r_squared": None,
            "residual_volatility_annualized": None,
            "estimates": {},
            "warning": "This historical backtest has no frozen attribution snapshot",
        }
        return {
            "id": record["id"],
            "frequency": "monthly",
            "observations": 0,
            "coverage": 0.0,
            "capm": {**empty_regression, "betas": {"MKT": None}},
            "multi_factor": empty_regression,
            "factor_return_correlation": {
                "labels": [],
                "observations": 0,
                "pearson": [],
                "spearman": [],
            },
            "selection_score_correlation": {"labels": [], "periods": 0, "median_spearman": []},
            "research_checks": {},
            "input_snapshot": [],
            "warnings": ["This historical backtest has no frozen attribution snapshot"],
        }
    return {"id": record["id"], **attribution}


def analyze_robustness(backtest_id: str) -> dict:
    record = get_backtest(backtest_id)
    if record is None:
        raise KeyError(backtest_id)
    config = _config_from_record(record)
    return_rows = sorted(record.get("returns", []), key=lambda item: item["date"])
    returns = pd.Series(
        [float(item.get("value") or 0.0) for item in return_rows],
        index=pd.to_datetime([item["date"] for item in return_rows]),
        dtype=float,
    )
    benchmark = _benchmark_returns(record, returns, recompute=True)
    rows = record.get("weights", [])
    if rows:
        frame = pd.DataFrame(rows)
        frame["date"] = pd.to_datetime(frame["date"])
        weights = frame.pivot_table(
            index="date",
            columns="symbol",
            values="weight",
            aggfunc="last",
            fill_value=0.0,
        ).sort_index()
    else:
        weights = pd.DataFrame(dtype=float)
    raw_thresholds = (
        dict((record.get("settings") or {}).get("research_thresholds") or {})
        if isinstance(record.get("settings"), dict)
        else {}
    )
    allowed_thresholds = set(RobustnessThresholds.__dataclass_fields__)
    thresholds = RobustnessThresholds(
        **{key: value for key, value in raw_thresholds.items() if key in allowed_thresholds}
    )
    report = robustness_report(
        returns,
        benchmark,
        weights,
        config,
        thresholds=thresholds,
        return_periods_per_year=_return_periods_per_year(returns.index),
    )
    return {
        "id": record["id"],
        "strategy_id": record.get("strategy_id"),
        "profile": record.get("profile") or "demo",
        "start_date": record.get("start_date"),
        "end_date": record.get("end_date"),
        **report,
    }


def analyze_signal_diagnostics(backtest_id: str) -> dict:
    """Derive selection evidence from one frozen BacktestRun."""

    record = get_backtest(backtest_id)
    if record is None:
        raise KeyError(backtest_id)
    diagnostics = (
        record.get("run_diagnostics") if isinstance(record.get("run_diagnostics"), dict) else {}
    )
    raw_evidence = diagnostics.get("signal_evidence")
    evidence = raw_evidence if isinstance(raw_evidence, dict) else {}
    sdk_rows = evidence.get("rows") if isinstance(evidence.get("rows"), list) else []
    if isinstance(raw_evidence, dict):
        rows = [dict(item) for item in sdk_rows if isinstance(item, dict)]
        ic_values = [float(item["ic"]) for item in rows if item.get("ic") is not None]
        coverage_values = [
            float(item["coverage"]) for item in rows if item.get("coverage") is not None
        ]
        turnover_values = [
            float(item["selection_turnover"])
            for item in rows
            if item.get("selection_turnover") is not None
        ]
        if ic_values:
            warning_code = None
            warning = None
        elif not rows:
            warning_code = "NO_SIGNAL_EVIDENCE"
            warning = "该回测没有产生可用信号截面。"
        elif max(int(item.get("ic_observations") or 0) for item in rows) < 3:
            warning_code = "INSUFFICIENT_CROSS_SECTION"
            warning = "每个信号截面可配对证券不足 3 只，不适合计算横截面 IC。"
        else:
            warning_code = "INSUFFICIENT_FORWARD_EVIDENCE"
            warning = "信号截面缺少可用的下一期收益证据，无法计算 IC。"
        return {
            "id": record["id"],
            "periods": len(rows),
            "evidence_periods": len(ic_values),
            "summary": {
                "mean_ic": _safe(np.mean(ic_values)) if ic_values else None,
                "positive_ic_ratio": (
                    _safe(np.mean([value > 0 for value in ic_values])) if ic_values else None
                ),
                "average_coverage": (_safe(np.mean(coverage_values)) if coverage_values else None),
                "average_selection_turnover": (
                    _safe(np.mean(turnover_values)) if turnover_values else None
                ),
            },
            "rows": rows,
            "warning_code": warning_code,
            "warning": warning,
        }

    # Persisted pre-evidence Runs retain their legacy frozen adapter. New SDK
    # Runs never reconstruct selection data from this shape.
    rows: list[dict[str, Any]] = []
    previous_selected: set[str] = set()
    for execution in record.get("executions", []):
        if not isinstance(execution, dict):
            continue
        outputs = execution.get("stage_outputs")
        outputs = outputs if isinstance(outputs, dict) else {}
        selection = outputs.get("selection") if isinstance(outputs.get("selection"), dict) else {}
        scores = _finite_mapping(selection.get("scores"))
        forward = _finite_mapping(execution.get("selection_forward_returns"))
        paired_symbols = sorted(set(scores) & set(forward))
        ic = None
        quantile_spread = None
        if len(paired_symbols) >= 3:
            score_values = pd.Series({symbol: scores[symbol] for symbol in paired_symbols})
            return_values = pd.Series({symbol: forward[symbol] for symbol in paired_symbols})
            correlation = score_values.rank().corr(return_values.rank())
            ic = _safe(correlation)
        if len(paired_symbols) >= 5:
            ordered = sorted(paired_symbols, key=scores.get)
            bucket = max(1, len(ordered) // 5)
            low = np.mean([forward[symbol] for symbol in ordered[:bucket]])
            high = np.mean([forward[symbol] for symbol in ordered[-bucket:]])
            quantile_spread = _safe(high - low)
        selected = {
            str(symbol) for symbol in selection.get("selected", []) if isinstance(symbol, str)
        }
        denominator = len(selected) + len(previous_selected)
        selection_turnover = (
            len(selected.symmetric_difference(previous_selected)) / denominator
            if previous_selected and denominator
            else None
        )
        eligibility = execution.get("eligibility") or execution.get("universe") or {}
        universe_count = int(eligibility.get("eligible_count") or 0)
        coverage = len(scores) / universe_count if universe_count else None
        rows.append(
            {
                "signal_date": execution.get("signal_date"),
                "universe_count": universe_count,
                "scored_count": len(scores),
                "selected_count": len(selected),
                "coverage": _safe(coverage),
                "ic": ic,
                "quantile_spread": quantile_spread,
                "selection_turnover": _safe(selection_turnover),
            }
        )
        previous_selected = selected

    ic_values = [float(row["ic"]) for row in rows if row["ic"] is not None]
    coverage_values = [float(row["coverage"]) for row in rows if row["coverage"] is not None]
    turnover_values = [
        float(row["selection_turnover"]) for row in rows if row["selection_turnover"] is not None
    ]
    return {
        "id": record["id"],
        "periods": len(rows),
        "evidence_periods": len(ic_values),
        "summary": {
            "mean_ic": _safe(np.mean(ic_values)) if ic_values else None,
            "positive_ic_ratio": (
                _safe(np.mean([value > 0 for value in ic_values])) if ic_values else None
            ),
            "average_coverage": _safe(np.mean(coverage_values)) if coverage_values else None,
            "average_selection_turnover": (
                _safe(np.mean(turnover_values)) if turnover_values else None
            ),
        },
        "rows": rows,
        "warning": (
            None if ic_values else "该历史回测未保存全体评分标的的前瞻收益，IC 与分组收益不可用。"
        ),
    }


def analyze_validation(backtest_id: str) -> dict:
    """Return named validation outputs frozen with one BacktestRun."""

    record = get_backtest(backtest_id)
    if record is None:
        raise KeyError(backtest_id)
    outputs = record.get("validation_output")
    if not isinstance(outputs, dict) or not outputs:
        return {
            "id": record["id"],
            "available_analyses": [],
            "outputs": {},
            "warnings": ["This historical backtest has no frozen validation output"],
        }
    return {
        "id": record["id"],
        "available_analyses": sorted(str(name) for name in outputs),
        "outputs": outputs,
        "warnings": [],
    }


def _finite_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, item in value.items():
        try:
            numeric = float(item)
        except (TypeError, ValueError):
            continue
        if np.isfinite(numeric):
            result[str(key)] = numeric
    return result


def _config_from_record(record: dict) -> StrategyConfig:
    source = record.get("strategy_source")
    if isinstance(source, str) and source.strip():
        sdk_manifest = record.get("strategy_manifest")
        if isinstance(sdk_manifest, list) and sdk_manifest:
            inspection = inspect_strategy_source(source)
            signal = next((item for item in sdk_manifest if item.get("kind") == "signal"), {})
            schedule = (signal.get("metadata") or {}).get("schedule") or {}
            frequency = schedule.get("frequency")
            parameters = _frozen_entrypoint_parameters(sdk_manifest, inspection, "execution")
            portfolio_parameters = _frozen_entrypoint_parameters(
                sdk_manifest, inspection, "portfolio"
            )
            settings = record.get("settings") if isinstance(record.get("settings"), dict) else {}
            symbols = sorted(
                {
                    str(item.get("symbol"))
                    for item in record.get("weights", [])
                    if item.get("symbol")
                }
            )
            holdings_count = max(
                1,
                max(
                    (
                        sum(1 for item in record.get("weights", []) if item.get("date") == date)
                        for date in {item.get("date") for item in record.get("weights", [])}
                    ),
                    default=1,
                ),
            )
            return StrategyConfig.from_dict(
                {
                    "name": record.get("strategy_project_id")
                    or record.get("strategy_id")
                    or "sdk-v1",
                    "universe": {"pool": "all", "symbols": symbols, "min_history_days": 2},
                    "selection": {
                        "n_stocks": holdings_count,
                        "signal_frequency": (
                            frequency if frequency in {"daily", "weekly", "monthly"} else "daily"
                        ),
                    },
                    "portfolio": {
                        "max_weight": float(
                            portfolio_parameters.get("max_weight", settings.get("max_weight", 1.0))
                        ),
                        "max_gross_exposure": float(
                            portfolio_parameters.get(
                                "max_gross_exposure",
                                settings.get("max_gross_exposure", 1.0),
                            )
                        ),
                    },
                    "execution": {
                        "cost_bps": float(parameters.get("commission_rate", 0.0) or 0.0) * 10_000.0,
                        "slippage_bps": float(parameters.get("slippage_rate", 0.0) or 0.0)
                        * 10_000.0,
                        "impact_bps": float(parameters.get("impact_rate", 0.0) or 0.0) * 10_000.0,
                        "execution_price": (
                            "next_open"
                            if parameters.get("activation", "next_session_open")
                            == "next_session_open"
                            else "next_close"
                        ),
                        "portfolio_value": float(settings.get("portfolio_value", 1_000_000.0)),
                        "max_participation_rate": float(
                            parameters.get("max_participation_rate", 0.1) or 0.1
                        ),
                    },
                    "metadata": {"research_trials": 1},
                }
            )
        manifest = record.get("component_manifest")
        settings = record.get("settings")
        if not isinstance(manifest, list) or not isinstance(settings, dict):
            raise ValueError("pipeline snapshot is incomplete")
        return _legacy_pipeline_config(
            record.get("pipeline_project_id") or record.get("strategy_id") or "snapshot",
            settings,
            manifest,
        )
    config_yaml = record.get("config_yaml")
    if not isinstance(config_yaml, str) or not config_yaml.strip():
        raise ValueError("backtest has no strategy snapshot")
    return legacy_config(config_yaml)


def _legacy_pipeline_config(
    project_id: str,
    settings: dict[str, Any],
    manifest: list[dict[str, Any]],
) -> StrategyConfig:
    """Reconstruct a read-only config from a persisted pre-SDK backtest snapshot."""

    parameters = {
        str(item.get("stage")): dict(item.get("parameters") or {})
        for item in manifest
        if isinstance(item, dict)
    }
    selection = parameters.get("selection", {})
    if isinstance(selection.get("selection"), dict):
        selection = {
            **selection["selection"],
            **{key: value for key, value in selection.items() if key != "selection"},
        }
    portfolio = parameters.get("portfolio", {})
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
    default_count = max(1, len(universe.get("symbols") or [])) or 20
    return StrategyConfig.from_dict(
        {
            "name": project_id,
            "description": "Persisted pre-SDK backtest snapshot",
            "universe": universe,
            "factors": factors,
            "selection": {
                "min_factor_coverage": float(selection.get("min_factor_coverage", 0.0)),
                "n_stocks": int(selection.get("count", default_count)),
                "signal_frequency": str(
                    selection.get("signal_frequency", execution.get("rebalance_freq", "monthly"))
                ),
                "normalization": str(selection.get("normalization", "percentile_rank")),
                "factor_weights": configured_weights,
                "exit_rank": int(selection.get("exit_rank", selection.get("count", default_count))),
            },
            "portfolio": {
                "max_weight": float(
                    portfolio.get("max_weight", legacy_risk.get("max_weight", 1.0))
                ),
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
            "metadata": {"legacy_snapshot": True},
        }
    )


def compare_backtests(backtest_ids: list[str]) -> dict:
    analyses = [analyze_backtest(backtest_id) for backtest_id in backtest_ids]
    all_dates = sorted({date for analysis in analyses for date in analysis["dates"]})
    series = {}
    labels = {}
    metrics = []
    for analysis in analyses:
        values = dict(zip(analysis["dates"], analysis["equity_curve"], strict=True))
        backtest_id = analysis["id"]
        series[backtest_id] = [values.get(date) for date in all_dates]
        labels[backtest_id] = (
            f"{analysis['strategy_id']} · "
            f"{str(analysis.get('start_date') or '')[:7]}–{str(analysis.get('end_date') or '')[:7]}"
        )
        metrics.append(
            {
                "id": backtest_id,
                "strategy_id": analysis["strategy_id"],
                "profile": analysis.get("profile") or "demo",
                "start_date": analysis.get("start_date"),
                "end_date": analysis.get("end_date"),
                "run_at": analysis.get("run_at"),
                **analysis["metrics"],
            }
        )
    return {
        "ids": backtest_ids,
        "dates": all_dates,
        "series": series,
        "labels": labels,
        "metrics": metrics,
    }
