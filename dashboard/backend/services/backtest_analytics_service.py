"""Derived analytics for persisted barebone backtests."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import yaml

from alphalab.analytics import equal_weight_benchmark, robustness_report
from alphalab.strategy import StrategyConfig, TimingStrategyConfig
from dashboard.backend.services.framework_service import _engine, get_backtest


def _safe(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    numeric = float(value)
    if not np.isfinite(numeric):
        return None
    return int(numeric) if isinstance(value, int) else numeric


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
            float(item["benchmark"])
            if item.get("benchmark") not in {None, ""}
            else np.nan
            for item in return_rows
        ],
        index=returns.index,
        dtype=float,
    )
    if not recompute or benchmark.empty or benchmark.notna().mean() >= 0.80:
        return benchmark
    config_yaml = record.get("config_yaml")
    if not isinstance(config_yaml, str) or not config_yaml.strip():
        return benchmark
    config = _config_from_yaml(config_yaml)
    engine = _engine(record.get("profile") or "demo")
    if isinstance(config, TimingStrategyConfig):
        return engine.get_factors(
            [config.market_factor],
            record["start_date"],
            record["end_date"],
            freq="1M",
            strict=True,
            use_cache=False,
        )[config.market_factor].reindex(returns.index)
    return equal_weight_benchmark(
        engine,
        list(config.universe.symbols) or engine.get_symbols(config.universe.pool),
        record["start_date"],
        record["end_date"],
        frequency=config.portfolio.rebalance_freq,
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
        changed = row.reindex(symbols, fill_value=0.0) - previous.reindex(
            symbols, fill_value=0.0
        )
        cash = 1.0 - float(row.sum())
        previous_cash = 1.0 - float(previous.sum())
        calculated_turnover = float(
            (changed.abs().sum() + abs(cash - previous_cash)) / 2.0
        )
        date_text = date.strftime("%Y-%m-%d")
        period_turnover = execution_turnover.get(date_text, calculated_turnover)
        turnover.append(
            {"date": date_text, "value": period_turnover}
        )
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

    metrics = {
        key: _safe(value)
        for key, value in (record.get("metrics") or {}).items()
    }
    config_yaml = record.get("config_yaml")
    strategy_snapshot = None
    if isinstance(config_yaml, str) and config_yaml.strip():
        config = _config_from_yaml(config_yaml)
        if isinstance(config, TimingStrategyConfig):
            strategy_snapshot = {
                "strategy_type": "market_timing",
                "implementation": config.implementation.kind,
                "name": config.name,
                "description": config.description,
                "factors": [],
                "signals": config.signal_names,
                "market_factor": config.market_factor,
                "rebalance_freq": "monthly",
                "execution_price": "monthly_factor_close",
                "cost_bps": config.execution.cost_bps,
                "max_exposure": config.position.max_exposure,
            }
        else:
            strategy_snapshot = {
                "strategy_type": "stock_selection",
                "implementation": config.implementation.kind,
                "name": config.name,
                "description": config.description,
                "factors": config.factor_names,
                "signals": [],
                "rebalance_freq": config.portfolio.rebalance_freq,
                "execution_price": config.execution.execution_price,
                "cost_bps": config.execution.cost_bps,
                "max_weight": config.portfolio.max_weight,
            }
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


def analyze_backtest(backtest_id: str) -> dict:
    record = get_backtest(backtest_id)
    if record is None:
        raise KeyError(backtest_id)
    return analyze_record(record)


def analyze_robustness(backtest_id: str) -> dict:
    record = get_backtest(backtest_id)
    if record is None:
        raise KeyError(backtest_id)
    config = _config_from_yaml(record["config_yaml"])
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
    report = robustness_report(returns, benchmark, weights, config)
    return {
        "id": record["id"],
        "strategy_id": record.get("strategy_id"),
        "profile": record.get("profile") or "demo",
        "start_date": record.get("start_date"),
        "end_date": record.get("end_date"),
        **report,
    }


def _config_from_yaml(yaml_text: str) -> StrategyConfig | TimingStrategyConfig:
    raw = yaml.safe_load(yaml_text) or {}
    if not isinstance(raw, dict):
        raise ValueError("strategy YAML must contain a mapping")
    if raw.get("strategy_type") == "market_timing":
        return TimingStrategyConfig.from_dict(raw)
    return StrategyConfig.from_dict(raw)


def compare_backtests(backtest_ids: list[str]) -> dict:
    analyses = [analyze_backtest(backtest_id) for backtest_id in backtest_ids]
    all_dates = sorted(
        {
            date
            for analysis in analyses
            for date in analysis["dates"]
        }
    )
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
