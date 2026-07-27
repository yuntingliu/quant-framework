"""Benchmark construction and honest robustness gates for saved backtests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from alphalab.analytics.metrics import PerformanceMetrics
from alphalab.dataio import DataEngine, to_wide
from alphalab.strategy import StrategyConfig


@dataclass(frozen=True)
class RobustnessThresholds:
    min_sharpe: float = 0.50
    min_annual_excess: float = 0.0
    min_positive_year_ratio: float = 0.60
    min_rolling_win_ratio: float = 0.55
    max_drawdown: float = -0.35
    stress_cost_bps: float = 50.0


def equal_weight_benchmark(
    engine: DataEngine,
    symbols: list[str],
    start_date: str,
    end_date: str,
    *,
    frequency: str = "monthly",
    warmup_days: int = 240,
) -> pd.Series:
    """Build a same-universe, next-period equal-weight benchmark."""

    start = pd.Timestamp(start_date)
    warmup = (start - pd.Timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    bars = engine.get_bars(
        symbols,
        warmup,
        end_date,
        fields=["close"],
        strict=False,
        use_cache=False,
    )
    if bars.empty:
        return pd.Series(dtype=float, name="benchmark")
    close = to_wide(bars, "close").sort_index()
    rule = "W-FRI" if frequency == "weekly" else pd.offsets.MonthEnd()
    prices = close.resample(rule).last().dropna(how="all")
    future_returns = prices.pct_change(fill_method=None).shift(-1)
    benchmark = future_returns.mean(axis=1, skipna=True)
    benchmark = benchmark.loc[
        (benchmark.index >= start) & (benchmark.index <= pd.Timestamp(end_date))
    ]
    benchmark.name = "benchmark"
    return benchmark.dropna()


def robustness_report(
    returns: pd.Series,
    benchmark: pd.Series,
    weights: pd.DataFrame,
    config: StrategyConfig,
    *,
    thresholds: RobustnessThresholds | None = None,
) -> dict:
    """Evaluate research quality without presenting the result as trading approval."""

    limits = thresholds or RobustnessThresholds()
    strategy = _clean_series(returns)
    reference = _clean_series(benchmark).reindex(strategy.index)
    aligned = pd.concat(
        [strategy.rename("strategy"), reference.rename("benchmark")],
        axis=1,
    )
    coverage = float(aligned["benchmark"].notna().mean()) if not aligned.empty else 0.0
    paired = aligned.dropna()
    excess = paired["strategy"] - paired["benchmark"]

    strategy_metrics = PerformanceMetrics.summarize(strategy)
    benchmark_metrics = PerformanceMetrics.summarize(paired["benchmark"])
    excess_metrics = PerformanceMetrics.summarize(excess)
    turnover = _turnover(weights)
    gross = strategy.add(
        turnover.reindex(strategy.index, fill_value=0.0)
        * (config.execution.cost_bps / 10000.0),
        fill_value=0.0,
    )
    costs = {
        str(int(cost)): PerformanceMetrics.summarize(
            gross
            - turnover.reindex(gross.index, fill_value=0.0) * (cost / 10000.0)
        )
        for cost in (10.0, 20.0, limits.stress_cost_bps)
    }

    annual = _annual_rows(paired)
    complete_years = [row for row in annual if row["complete"]]
    positive_year_ratio = (
        float(np.mean([row["excess"] > 0 for row in complete_years]))
        if complete_years
        else 0.0
    )
    rolling = _rolling_rows(paired, windows=(12, 24))
    rolling_12 = rolling["12"]
    rolling_win_ratio = (
        float(np.mean([row["excess"] > 0 for row in rolling_12]))
        if rolling_12
        else 0.0
    )

    weight_checks = _weight_checks(weights, config)
    checks = [
        _check("non_empty_returns", not strategy.empty, f"{len(strategy)} periods"),
        _check("finite_returns", bool(np.isfinite(strategy).all()), "returns must be finite"),
        _check("benchmark_coverage", coverage >= 0.80, f"{coverage:.1%} coverage"),
        *weight_checks,
    ]
    invalid = any(not item["passed"] for item in checks)
    annual_excess = float(excess_metrics.get("annual_return") or 0.0)
    sharpe = float(strategy_metrics.get("sharpe") or 0.0)
    drawdown = float(strategy_metrics.get("max_drawdown") or 0.0)
    stressed_excess = _annual_return(
        costs[str(int(limits.stress_cost_bps))],
        benchmark_metrics,
    )

    candidate_rules = {
        "annual_excess": annual_excess > limits.min_annual_excess,
        "sharpe": sharpe >= limits.min_sharpe,
        "max_drawdown": drawdown >= limits.max_drawdown,
        "positive_year_ratio": positive_year_ratio >= limits.min_positive_year_ratio,
        "rolling_win_ratio": rolling_win_ratio >= limits.min_rolling_win_ratio,
        "stress_cost_excess": stressed_excess > limits.min_annual_excess,
    }
    if invalid:
        status = "invalid"
    elif annual_excess <= 0 or sharpe <= 0:
        status = "weak"
    elif all(candidate_rules.values()):
        status = "research_candidate"
    else:
        status = "watch"

    return {
        "status": status,
        "disclaimer": "Research gate only; this is not an approval for live trading.",
        "periods": int(len(strategy)),
        "benchmark_coverage": coverage,
        "metrics": {
            "strategy": _finite_dict(strategy_metrics),
            "benchmark": _finite_dict(benchmark_metrics),
            "excess": _finite_dict(excess_metrics),
        },
        "candidate_rules": candidate_rules,
        "checks": checks,
        "annual": annual,
        "rolling": rolling,
        "cost_sensitivity": {
            key: _finite_dict(value) for key, value in costs.items()
        },
        "turnover": {
            "average": _finite(turnover.mean()) if not turnover.empty else None,
            "maximum": _finite(turnover.max()) if not turnover.empty else None,
        },
        "portfolio": _portfolio_summary(weights),
        "thresholds": {
            "min_sharpe": limits.min_sharpe,
            "min_annual_excess": limits.min_annual_excess,
            "min_positive_year_ratio": limits.min_positive_year_ratio,
            "min_rolling_win_ratio": limits.min_rolling_win_ratio,
            "max_drawdown": limits.max_drawdown,
            "stress_cost_bps": limits.stress_cost_bps,
        },
    }


def _clean_series(values: pd.Series) -> pd.Series:
    result = pd.to_numeric(values, errors="coerce")
    result.index = pd.to_datetime(result.index)
    return result.replace([np.inf, -np.inf], np.nan).dropna().sort_index()


def _turnover(weights: pd.DataFrame) -> pd.Series:
    if weights.empty:
        return pd.Series(dtype=float)
    values = weights.fillna(0.0).sort_index().astype(float)
    previous = values.shift(1, fill_value=0.0)
    return (values - previous).abs().sum(axis=1) / 2.0


def _weight_checks(weights: pd.DataFrame, config: StrategyConfig) -> list[dict]:
    if weights.empty:
        return [_check("weights_present", False, "no saved weights")]
    values = weights.fillna(0.0).astype(float)
    gross = values.abs().sum(axis=1)
    maximum = values.max(axis=1)
    return [
        _check("weights_present", True, f"{len(values)} snapshots"),
        _check(
            "gross_exposure",
            bool((gross <= 1.000001).all() and (gross >= 0.99).all()),
            f"range {gross.min():.4f}-{gross.max():.4f}",
        ),
        _check(
            "max_weight",
            bool((maximum <= config.portfolio.max_weight + 1e-8).all()),
            f"observed {maximum.max():.4f}, limit {config.portfolio.max_weight:.4f}",
        ),
    ]


def _annual_rows(frame: pd.DataFrame) -> list[dict]:
    rows = []
    for year, group in frame.groupby(frame.index.year):
        strategy = float((1.0 + group["strategy"]).prod() - 1.0)
        benchmark = float((1.0 + group["benchmark"]).prod() - 1.0)
        rows.append(
            {
                "year": int(year),
                "strategy": strategy,
                "benchmark": benchmark,
                "excess": strategy - benchmark,
                "periods": int(len(group)),
                "complete": bool(len(group) >= 10),
            }
        )
    return rows


def _rolling_rows(frame: pd.DataFrame, windows: tuple[int, ...]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for window in windows:
        values = []
        if len(frame) >= window:
            strategy = (1.0 + frame["strategy"]).rolling(window).apply(np.prod, raw=True) - 1.0
            benchmark = (1.0 + frame["benchmark"]).rolling(window).apply(np.prod, raw=True) - 1.0
            for date in frame.index:
                if pd.notna(strategy.loc[date]) and pd.notna(benchmark.loc[date]):
                    values.append(
                        {
                            "date": date.strftime("%Y-%m-%d"),
                            "strategy": float(strategy.loc[date]),
                            "benchmark": float(benchmark.loc[date]),
                            "excess": float(strategy.loc[date] - benchmark.loc[date]),
                        }
                    )
        result[str(window)] = values
    return result


def _portfolio_summary(weights: pd.DataFrame) -> dict:
    if weights.empty:
        return {
            "average_holdings": None,
            "average_concentration": None,
            "maximum_weight": None,
        }
    values = weights.fillna(0.0).astype(float)
    return {
        "average_holdings": _finite((values.abs() > 1e-12).sum(axis=1).mean()),
        "average_concentration": _finite((values**2).sum(axis=1).mean()),
        "maximum_weight": _finite(values.max(axis=1).max()),
    }


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _annual_return(strategy: dict, benchmark: dict) -> float:
    return float(strategy.get("annual_return") or 0.0) - float(
        benchmark.get("annual_return") or 0.0
    )


def _finite(value: Any) -> float | int | None:
    if value is None:
        return None
    numeric = float(value)
    if not np.isfinite(numeric):
        return None
    return int(numeric) if isinstance(value, (int, np.integer)) else numeric


def _finite_dict(values: dict) -> dict:
    return {key: _finite(value) for key, value in values.items()}


__all__ = [
    "RobustnessThresholds",
    "equal_weight_benchmark",
    "robustness_report",
]
