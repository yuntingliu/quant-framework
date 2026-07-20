"""Derived analytics for persisted barebone backtests."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from alphalab.analytics import equal_weight_benchmark, robustness_report
from alphalab.strategy import StrategyConfig
from dashboard.backend.services.framework_service import _engine, get_backtest


def _safe(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    numeric = float(value)
    if not np.isfinite(numeric):
        return None
    return int(numeric) if isinstance(value, int) else numeric


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
    previous = pd.Series(dtype=float)
    for date, row in pivot.iterrows():
        current = row.loc[row.abs() > 1e-12]
        symbols = previous.index.union(row.index)
        changed = row.reindex(symbols, fill_value=0.0) - previous.reindex(
            symbols, fill_value=0.0
        )
        period_turnover = float(changed.abs().sum() / 2.0)
        turnover.append(
            {"date": date.strftime("%Y-%m-%d"), "value": period_turnover}
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
        "turnover": turnover,
        "average_turnover": (
            _safe(np.mean([item["value"] for item in turnover])) if turnover else None
        ),
        "holdings": snapshots,
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
    config = StrategyConfig.from_yaml_string(record["config_yaml"])
    return_rows = sorted(record.get("returns", []), key=lambda item: item["date"])
    returns = pd.Series(
        [float(item.get("value") or 0.0) for item in return_rows],
        index=pd.to_datetime([item["date"] for item in return_rows]),
        dtype=float,
    )
    benchmark = pd.Series(
        [
            float(item["benchmark"]) if item.get("benchmark") not in {None, ""} else np.nan
            for item in return_rows
        ],
        index=returns.index,
        dtype=float,
    )
    if benchmark.notna().mean() < 0.80:
        engine = _engine(record.get("profile") or "demo")
        benchmark = equal_weight_benchmark(
            engine,
            list(config.universe.symbols) or engine.get_symbols(config.universe.pool),
            record["start_date"],
            record["end_date"],
            frequency=config.portfolio.rebalance_freq,
        ).reindex(returns.index)
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
        labels[backtest_id] = analysis["strategy_id"]
        metrics.append(
            {
                "id": backtest_id,
                "strategy_id": analysis["strategy_id"],
                "profile": analysis.get("profile") or "demo",
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
