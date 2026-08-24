"""Benchmark construction and honest robustness gates for saved backtests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from alphalab.analytics.inference import (
    moving_block_mean_interval,
    newey_west_one_sided_p_value,
)
from alphalab.analytics.metrics import PerformanceMetrics
from alphalab.dataio import DataEngine
from alphalab.strategy import StrategyConfig


@dataclass(frozen=True)
class RobustnessThresholds:
    min_sharpe: float = 0.50
    min_annual_excess: float = 0.0
    min_positive_year_ratio: float = 0.60
    min_rolling_win_ratio: float = 0.55
    max_drawdown: float = -0.35
    stress_cost_bps: float = 50.0
    min_periods: int = 24
    validation_fraction: float = 0.30
    max_adjusted_p_value: float = 0.05


def equal_weight_benchmark(
    engine: DataEngine,
    symbols: list[str],
    start_date: str,
    end_date: str,
    *,
    frequency: str = "monthly",
    warmup_days: int = 240,
    execution_price: str = "next_open",
) -> pd.Series:
    """Build a same-universe benchmark on the strategy execution calendar."""

    start = pd.Timestamp(start_date)
    warmup = (start - pd.Timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    bars = engine.get_bars(
        symbols,
        warmup,
        end_date,
        fields=["open", "close"],
        strict=False,
        use_cache=False,
    )
    if bars.empty:
        return pd.Series(dtype=float, name="benchmark")
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"])
    sessions = pd.DatetimeIndex(bars["date"].dropna().unique()).sort_values()
    sessions = sessions[sessions <= pd.Timestamp(end_date)]
    if frequency == "daily":
        signals = pd.Series(sessions, index=sessions)
    elif frequency == "weekly":
        signals = pd.Series(sessions, index=sessions).groupby(sessions.to_period("W-FRI")).max()
    else:
        signals = pd.Series(sessions, index=sessions).groupby(sessions.to_period("M")).max()
    schedule: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for raw_signal in signals:
        signal = pd.Timestamp(raw_signal)
        if signal < start or signal >= pd.Timestamp(end_date):
            continue
        position = int(sessions.searchsorted(signal, side="right"))
        if position < len(sessions):
            schedule.append((signal, pd.Timestamp(sessions[position])))
    field = "open" if execution_price == "next_open" else "close"
    values: dict[pd.Timestamp, float] = {}
    for index, (signal_date, entry_date) in enumerate(schedule[:-1]):
        _, exit_date = schedule[index + 1]
        instruments = engine.get_instruments(signal_date.strftime("%Y-%m-%d"))
        eligible_symbols = (
            set(instruments["symbol"].astype(str).str.upper())
            if not instruments.empty
            else set(symbols)
        )
        period_returns: list[float] = []
        for symbol, frame in bars.groupby("symbol"):
            if str(symbol).upper() not in eligible_symbols:
                continue
            ordered = frame.sort_values("date")
            entry_rows = ordered.loc[ordered["date"].le(entry_date)]
            exit_rows = ordered.loc[ordered["date"].le(exit_date)]
            if entry_rows.empty or exit_rows.empty:
                continue
            entry_row = entry_rows.iloc[-1]
            exit_row = exit_rows.iloc[-1]
            entry_value = entry_row.get(field) if pd.Timestamp(entry_row["date"]) == entry_date else entry_row.get("close")
            exit_value = exit_row.get(field) if pd.Timestamp(exit_row["date"]) == exit_date else exit_row.get("close")
            entry_price = pd.to_numeric(pd.Series([entry_value]), errors="coerce").iloc[0]
            exit_price = pd.to_numeric(pd.Series([exit_value]), errors="coerce").iloc[0]
            if pd.notna(entry_price) and pd.notna(exit_price) and float(entry_price) > 0:
                period_returns.append(float(exit_price / entry_price - 1.0))
        if period_returns:
            values[entry_date] = float(np.mean(period_returns))
    return pd.Series(values, name="benchmark", dtype=float).sort_index()


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
    periods_per_year = _periods_per_year(config)
    minimum_periods = max(limits.min_periods, periods_per_year * 2)
    strategy = _clean_series(returns)
    reference = _clean_series(benchmark).reindex(strategy.index)
    aligned = pd.concat(
        [strategy.rename("strategy"), reference.rename("benchmark")],
        axis=1,
    )
    coverage = float(aligned["benchmark"].notna().mean()) if not aligned.empty else 0.0
    paired = aligned.dropna()
    excess = paired["strategy"] - paired["benchmark"]

    strategy_metrics = PerformanceMetrics.summarize(strategy, periods_per_year)
    benchmark_metrics = PerformanceMetrics.summarize(
        paired["benchmark"], periods_per_year
    )
    excess_metrics = PerformanceMetrics.summarize(excess, periods_per_year)
    turnover = _turnover(weights)
    traded_weight = _traded_weight(weights)
    gross = strategy.add(
        traded_weight.reindex(strategy.index, fill_value=0.0)
        * ((config.execution.cost_bps + config.execution.slippage_bps) / 10000.0),
        fill_value=0.0,
    )
    costs = {
        str(int(cost)): PerformanceMetrics.summarize(
            gross
            - traded_weight.reindex(gross.index, fill_value=0.0) * (cost / 10000.0),
            periods_per_year,
        )
        for cost in (10.0, 20.0, limits.stress_cost_bps)
    }

    annual = _annual_rows(paired, periods_per_year)
    complete_years = [row for row in annual if row["complete"]]
    positive_year_ratio = (
        float(np.mean([row["excess"] > 0 for row in complete_years]))
        if complete_years
        else 0.0
    )
    rolling = _rolling_rows(
        paired,
        windows=(periods_per_year, periods_per_year * 2),
    )
    rolling_primary = rolling[str(periods_per_year)]
    rolling_win_ratio = (
        float(np.mean([row["excess"] > 0 for row in rolling_primary]))
        if rolling_primary
        else 0.0
    )
    validation = _validation_split(
        paired,
        limits.validation_fraction,
        periods_per_year,
    )
    bootstrap_excess = moving_block_mean_interval(
        excess,
        draws=5_000,
        minimum_observations=12,
    )
    trials = max(1, int(config.metadata.get("research_trials", 1) or 1))
    p_value = newey_west_one_sided_p_value(excess)
    adjusted_p_value = min(1.0, p_value * trials)

    weight_checks = _weight_checks(weights, config)
    checks = [
        _check("non_empty_returns", not strategy.empty, f"{len(strategy)} periods"),
        _check(
            "minimum_history",
            len(strategy) >= minimum_periods,
            f"{len(strategy)} / {minimum_periods} periods",
        ),
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
        "validation_excess": float(
            validation.get("validation", {}).get("excess", {}).get("annual_return") or 0.0
        ) > limits.min_annual_excess,
        "bootstrap_excess": (
            bootstrap_excess["lower"] is not None
            and float(bootstrap_excess["lower"]) > 0
        ),
        "multiple_testing": adjusted_p_value <= limits.max_adjusted_p_value,
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
        "validation": validation,
        "statistical": {
            "bootstrap_mean_excess_95": bootstrap_excess,
            "one_sided_p_value": p_value,
            "research_trials": trials,
            "adjusted_p_value": adjusted_p_value,
        },
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
            "min_periods": minimum_periods,
            "validation_fraction": limits.validation_fraction,
            "max_adjusted_p_value": limits.max_adjusted_p_value,
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
    cash = 1.0 - values.sum(axis=1)
    previous_cash = 1.0 - previous.sum(axis=1)
    return ((values - previous).abs().sum(axis=1) + (cash - previous_cash).abs()) / 2.0


def _traded_weight(weights: pd.DataFrame) -> pd.Series:
    if weights.empty:
        return pd.Series(dtype=float)
    values = weights.fillna(0.0).sort_index().astype(float)
    previous = values.shift(1, fill_value=0.0)
    return (values - previous).abs().sum(axis=1)


def _weight_checks(
    weights: pd.DataFrame,
    config: StrategyConfig,
) -> list[dict]:
    if weights.empty:
        return [_check("weights_present", False, "no saved weights")]
    values = weights.fillna(0.0).astype(float)
    gross = values.abs().sum(axis=1)
    maximum = values.max(axis=1)
    maximum_limit = config.portfolio.max_weight
    return [
        _check("weights_present", True, f"{len(values)} snapshots"),
        _check(
            "gross_exposure",
            bool((gross <= 1.000001).all() and (gross >= -1e-12).all()),
            f"range {gross.min():.4f}-{gross.max():.4f}; remainder is cash",
        ),
        _check(
            "max_weight",
            bool((maximum <= maximum_limit + 1e-8).all()),
            f"observed {maximum.max():.4f}, limit {maximum_limit:.4f}",
        ),
    ]


def _periods_per_year(config: StrategyConfig) -> int:
    if config.execution.rebalance_freq == "daily":
        return 252
    return 52 if config.execution.rebalance_freq == "weekly" else 12


def _annual_rows(frame: pd.DataFrame, periods_per_year: int) -> list[dict]:
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
                "complete": bool(
                    len(group) >= (40 if periods_per_year == 52 else 10)
                ),
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


def _validation_split(
    frame: pd.DataFrame,
    fraction: float,
    periods_per_year: int,
) -> dict:
    if frame.empty:
        return {"split_date": None, "development": {}, "validation": {}}
    validation_size = max(1, int(np.ceil(len(frame) * fraction)))
    split = max(1, len(frame) - validation_size)
    development = frame.iloc[:split]
    validation = frame.iloc[split:]
    return {
        "split_date": validation.index.min().strftime("%Y-%m-%d"),
        "development": _period_metrics(development, periods_per_year),
        "validation": _period_metrics(validation, periods_per_year),
    }


def _period_metrics(frame: pd.DataFrame, periods_per_year: int) -> dict:
    paired = frame.dropna()
    return {
        "periods": len(paired),
        "strategy": _finite_dict(
            PerformanceMetrics.summarize(paired["strategy"], periods_per_year)
        ),
        "benchmark": _finite_dict(
            PerformanceMetrics.summarize(paired["benchmark"], periods_per_year)
        ),
        "excess": _finite_dict(
            PerformanceMetrics.summarize(
                paired["strategy"] - paired["benchmark"],
                periods_per_year,
            )
        ),
    }


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
