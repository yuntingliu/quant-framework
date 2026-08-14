"""Point-in-time cross-sectional factor diagnostics."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from alphalab.analytics.inference import (
    moving_block_mean_interval,
    newey_west_mean_t_stat,
)
from alphalab.analytics.metrics import PerformanceMetrics
from alphalab.dataio import DataEngine, to_wide
from alphalab.engine import SignalEngine
from alphalab.factors.cross_sectional import (
    compute_cross_sectional_factor,
    required_fundamental_fields,
)
from alphalab.strategy import FactorSpec, StrategyConfig, UniverseSpec


def evaluate_factor(
    engine: DataEngine,
    factor: FactorSpec,
    start_date: str,
    end_date: str,
    *,
    universe: UniverseSpec | None = None,
    frequency: str = "monthly",
    quantiles: int = 5,
    lookback_days: int = 252,
) -> dict:
    """Evaluate one factor using PIT snapshots and subsequent period returns."""

    if frequency not in {"monthly", "weekly"}:
        raise ValueError("factor research frequency must be monthly or weekly")
    if not 3 <= quantiles <= 10:
        raise ValueError("factor research quantiles must be between 3 and 10")
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    if start >= end:
        raise ValueError("factor research start_date must be before end_date")
    universe_spec = universe or UniverseSpec()
    symbols = list(universe_spec.symbols) or engine.get_symbols(universe_spec.pool)
    if not symbols:
        return _empty_result(factor, frequency, quantiles, "empty universe")

    warmup = (start - pd.Timedelta(days=max(lookback_days * 2, 400))).strftime("%Y-%m-%d")
    bars = engine.get_bars(
        symbols,
        warmup,
        end.strftime("%Y-%m-%d"),
        fields=["close", "volume", "amount"],
        strict=False,
        use_cache=False,
    )
    if bars.empty:
        return _empty_result(factor, frequency, quantiles, "no market bars")
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"])
    signal_dates = _signal_dates(bars["date"], start, end, frequency)
    if len(signal_dates) < 2:
        return _empty_result(factor, frequency, quantiles, "insufficient periods")
    close = to_wide(bars[["date", "symbol", "close"]], "close").sort_index()
    sampled_close = close.reindex(signal_dates)
    horizons = {
        horizon: sampled_close.shift(-horizon).div(sampled_close).sub(1.0)
        for horizon in (1, 3, 6)
    }

    fundamental_fields = required_fundamental_fields((factor,))
    fundamentals = pd.DataFrame()
    if fundamental_fields:
        first_quarter = f"{max(1900, start.year - 3)}q1"
        last_quarter = f"{end.year}q4"
        fundamentals = engine.get_fundamentals(
            symbols,
            fundamental_fields,
            first_quarter,
            last_quarter,
            asof_date=None,
            strict=False,
            use_cache=False,
        )
        if "available_date" in fundamentals:
            fundamentals = fundamentals.copy()
            fundamentals["available_date"] = pd.to_datetime(
                fundamentals["available_date"],
                errors="coerce",
            )

    config = StrategyConfig(
        name="factor_research",
        universe=universe_spec,
        factors=(replace(factor, weight=1.0),),
    )
    grouped = {
        symbol: frame.sort_values("date").reset_index(drop=True)
        for symbol, frame in bars.groupby("symbol")
    }
    rows: list[dict] = []
    horizon_ics: dict[int, list[float]] = {1: [], 3: [], 6: []}
    previous_top: set[str] = set()
    instrument_filter_periods = 0
    future_instrument_snapshot = False
    for signal_date in signal_dates[:-1]:
        data_by_symbol = {
            symbol: frame.loc[frame["date"].le(signal_date)].tail(lookback_days).copy()
            for symbol, frame in grouped.items()
            if bool(frame["date"].le(signal_date).any())
        }
        instruments = engine.get_instruments(signal_date.strftime("%Y-%m-%d"))
        if not instruments.empty:
            instrument_filter_periods += 1
            if "snapshot_date" in instruments and instruments["snapshot_date"].notna().any():
                future_instrument_snapshot = future_instrument_snapshot or bool(
                    pd.Timestamp(instruments["snapshot_date"].max()) > signal_date
                )
            instrument_symbols = set(instruments["symbol"].astype(str).str.upper())
            data_by_symbol = {
                symbol: frame
                for symbol, frame in data_by_symbol.items()
                if symbol in instrument_symbols
            }
        data_by_symbol, exclusions = SignalEngine._eligible_data(
            config,
            data_by_symbol,
            signal_date,
        )
        if not data_by_symbol:
            continue
        pit = fundamentals
        if not fundamentals.empty and "available_date" in fundamentals:
            pit = fundamentals.loc[fundamentals["available_date"].le(signal_date)]
        values = compute_cross_sectional_factor(factor, data_by_symbol, pit)
        if factor.direction == "short":
            values = -values
        one_period = horizons[1].loc[signal_date] if signal_date in horizons[1].index else pd.Series(dtype=float)
        aligned = pd.concat(
            [values.rename("factor"), one_period.rename("forward_return")],
            axis=1,
        ).replace([np.inf, -np.inf], np.nan).dropna()
        if len(aligned) < max(quantiles * 2, 10):
            continue
        ic = float(aligned["factor"].corr(aligned["forward_return"], method="spearman"))
        if not np.isfinite(ic):
            continue
        ranks = aligned["factor"].rank(method="first")
        buckets = pd.qcut(ranks, quantiles, labels=False) + 1
        quantile_returns = aligned.groupby(buckets)["forward_return"].mean()
        top_members = set(aligned.index[buckets.eq(quantiles)])
        turnover = (
            1.0
            - len(previous_top & top_members)
            / max(len(previous_top), len(top_members))
            if previous_top and top_members
            else None
        )
        previous_top = top_members
        for horizon, forward in horizons.items():
            if signal_date not in forward.index:
                continue
            horizon_frame = pd.concat(
                [values.rename("factor"), forward.loc[signal_date].rename("forward_return")],
                axis=1,
            ).replace([np.inf, -np.inf], np.nan).dropna()
            if len(horizon_frame) >= 10:
                horizon_ic = horizon_frame["factor"].corr(
                    horizon_frame["forward_return"],
                    method="spearman",
                )
                if pd.notna(horizon_ic):
                    horizon_ics[horizon].append(float(horizon_ic))
        row = {
            "date": signal_date.strftime("%Y-%m-%d"),
            "observations": int(len(aligned)),
            "coverage": float(len(aligned) / max(1, len(symbols))),
            "ic": ic,
            "long_short": float(quantile_returns.loc[quantiles] - quantile_returns.loc[1]),
            "top_turnover": turnover,
            "exclusions": exclusions,
            "quantile_returns": {
                str(bucket): float(quantile_returns.loc[bucket])
                for bucket in quantile_returns.index
            },
        }
        rows.append(row)

    if not rows:
        return _empty_result(factor, frequency, quantiles, "no valid cross-sections")
    ic_values = pd.Series([row["ic"] for row in rows], dtype=float)
    long_short = pd.Series([row["long_short"] for row in rows], dtype=float)
    periods_per_year = 52 if frequency == "weekly" else 12
    ic_std = float(ic_values.std(ddof=1)) if len(ic_values) > 1 else 0.0
    bootstrap = moving_block_mean_interval(
        ic_values,
        draws=2_000,
        minimum_observations=6,
    )
    return {
        "factor": {
            "name": factor.name,
            "source": factor.source,
            "expression": factor.expression,
            "direction": factor.direction,
            "winsorize": factor.winsorize,
            "neutralize": list(factor.neutralize),
        },
        "frequency": frequency,
        "quantiles": quantiles,
        "universe_size": len(symbols),
        "periods": len(rows),
        "summary": {
            "ic_mean": float(ic_values.mean()),
            "ic_std": ic_std,
            "icir": float(ic_values.mean() / ic_std * np.sqrt(periods_per_year)) if ic_std > 0 else 0.0,
            "ic_t_stat": newey_west_mean_t_stat(ic_values),
            "ic_positive_ratio": float(ic_values.gt(0).mean()),
            "coverage_mean": float(np.mean([row["coverage"] for row in rows])),
            "top_turnover_mean": _optional_mean([row["top_turnover"] for row in rows]),
            "bootstrap_ic_95": bootstrap,
            "long_short": PerformanceMetrics.summarize(
                long_short,
                periods_per_year=periods_per_year,
            ),
        },
        "decay": {
            str(horizon): {
                "mean_ic": float(np.mean(values)) if values else None,
                "observations": len(values),
            }
            for horizon, values in horizon_ics.items()
        },
        "rows": rows,
        "warnings": _warnings(
            rows,
            bootstrap,
            instrument_filter_periods,
            future_instrument_snapshot,
        ),
    }


def _signal_dates(
    dates: pd.Series,
    start: pd.Timestamp,
    end: pd.Timestamp,
    frequency: str,
) -> pd.DatetimeIndex:
    unique = pd.DatetimeIndex(pd.to_datetime(dates).dropna().unique()).sort_values()
    unique = unique[(unique >= start) & (unique <= end)]
    if frequency == "weekly":
        grouped = pd.Series(unique, index=unique).groupby(unique.to_period("W-FRI")).max()
    else:
        grouped = pd.Series(unique, index=unique).groupby(unique.to_period("M")).max()
    return pd.DatetimeIndex(grouped.to_list())


def _optional_mean(values: list[float | None]) -> float | None:
    finite = [float(value) for value in values if value is not None and np.isfinite(value)]
    return float(np.mean(finite)) if finite else None


def _warnings(
    rows: list[dict],
    bootstrap: dict[str, float | None],
    instrument_filter_periods: int,
    future_instrument_snapshot: bool,
) -> list[str]:
    warnings: list[str] = []
    if len(rows) < 24:
        warnings.append("Fewer than 24 evaluation periods; inference is unstable")
    if float(np.mean([row["coverage"] for row in rows])) < 0.6:
        warnings.append("Average factor coverage is below 60%")
    if bootstrap["lower"] is None or float(bootstrap["lower"]) <= 0:
        warnings.append("The 95% bootstrap interval for mean IC is not strictly positive")
    if instrument_filter_periods == 0:
        warnings.append(
            "PIT instrument snapshots were unavailable; the universe came from bar history"
        )
    elif future_instrument_snapshot:
        warnings.append(
            "At least one instrument snapshot post-dates its factor observation"
        )
    return warnings


def _empty_result(
    factor: FactorSpec,
    frequency: str,
    quantiles: int,
    warning: str,
) -> dict:
    return {
        "factor": {
            "name": factor.name,
            "source": factor.source,
            "expression": factor.expression,
            "direction": factor.direction,
            "winsorize": factor.winsorize,
            "neutralize": list(factor.neutralize),
        },
        "frequency": frequency,
        "quantiles": quantiles,
        "universe_size": 0,
        "periods": 0,
        "summary": {},
        "decay": {},
        "rows": [],
        "warnings": [warning],
    }


__all__ = ["evaluate_factor"]
