"""Leakage-aware evidence for saved Strategy SDK v1 factor snapshots."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from alphalab.analytics.inference import moving_block_mean_interval, newey_west_mean_t_stat
from alphalab.analytics.metrics import PerformanceMetrics


def factor_research_report(
    snapshots: Sequence[Mapping[str, Any]],
    bars: pd.DataFrame,
    *,
    frequency: str,
    quantiles: int = 5,
    horizons: Sequence[int] = (1, 3, 6),
    validation_fraction: float = 0.30,
) -> dict[str, Any]:
    """Evaluate point-in-time factor snapshots against later open-to-open returns."""

    if frequency not in {"daily", "weekly", "monthly"}:
        raise ValueError("frequency must be daily, weekly, or monthly")
    if not 3 <= int(quantiles) <= 10:
        raise ValueError("quantiles must be between 3 and 10")
    normalized_horizons = tuple(sorted({int(value) for value in horizons}))
    if not normalized_horizons or normalized_horizons[0] < 1 or normalized_horizons[-1] > 12:
        raise ValueError("horizons must contain values between 1 and 12")
    if 1 not in normalized_horizons:
        raise ValueError("horizons must include the primary one-period horizon")
    if not 0.10 <= float(validation_fraction) <= 0.50:
        raise ValueError("validation_fraction must be between 0.10 and 0.50")

    market = pd.DataFrame(bars).copy()
    required_columns = {"date", "symbol", "open"}
    if not required_columns.issubset(market.columns):
        missing = sorted(required_columns - set(market.columns))
        raise ValueError(f"factor research requires market columns: {missing}")
    market["date"] = pd.to_datetime(market["date"], errors="coerce").dt.normalize()
    market["symbol"] = market["symbol"].astype(str).str.upper()
    market["open"] = pd.to_numeric(market["open"], errors="coerce")
    market = market.loc[
        market["date"].notna() & market["open"].gt(0) & np.isfinite(market["open"])
    ]
    sessions = pd.DatetimeIndex(market["date"].unique()).sort_values()
    opens = market.pivot_table(
        index="date", columns="symbol", values="open", aggfunc="last"
    ).reindex(sessions)
    ordered_snapshots = sorted(
        (dict(snapshot) for snapshot in snapshots), key=lambda item: str(item.get("date") or "")
    )
    signal_dates = [pd.Timestamp(item["date"]).normalize() for item in ordered_snapshots]
    entry_dates = [_next_session(sessions, value) for value in signal_dates]
    rows_by_horizon: dict[int, list[dict[str, Any]]] = {
        horizon: [] for horizon in normalized_horizons
    }
    previous_top: set[str] = set()

    for position, snapshot in enumerate(ordered_snapshots):
        entry_date = entry_dates[position]
        if entry_date is None:
            continue
        values, symbol_count = _snapshot_values(snapshot.get("values"))
        minimum = max(int(quantiles) * 2, 10)
        if len(values) < minimum:
            continue
        # Formation uses only scores available at the signal date.
        buckets = pd.qcut(values.rank(method="first"), int(quantiles), labels=False) + 1
        top_members = set(values.index[buckets.eq(int(quantiles))])
        denominator = len(previous_top) + len(top_members)
        turnover = (len(previous_top.symmetric_difference(top_members)) / denominator
                    if previous_top and denominator else None)
        previous_top = top_members
        for horizon in normalized_horizons:
            exit_position = position + horizon
            if exit_position >= len(entry_dates):
                continue
            exit_date = entry_dates[exit_position]
            if exit_date is None or exit_date <= entry_date:
                continue
            forward = opens.loc[exit_date].div(opens.loc[entry_date]).sub(1.0)
            aligned = pd.concat(
                [values.rename("factor"), forward.rename("forward_return")], axis=1
            ).replace([np.inf, -np.inf], np.nan).dropna()
            minimum = max(int(quantiles) * 2, 10)
            if len(aligned) < minimum:
                continue
            grouped = aligned.groupby(buckets)["forward_return"].mean()
            rank_ic = aligned["factor"].corr(aligned["forward_return"], method="spearman")
            if pd.isna(rank_ic) or not np.isfinite(float(rank_ic)):
                continue
            rows_by_horizon[horizon].append(
                {
                    "signal_date": signal_dates[position].strftime("%Y-%m-%d"),
                    "entry_date": entry_date.strftime("%Y-%m-%d"),
                    "exit_date": exit_date.strftime("%Y-%m-%d"),
                    "observations": int(len(aligned)),
                    "formation_count": int(len(values)),
                    "missing_forward_count": int(len(values) - len(aligned)),
                    "coverage": float(len(aligned) / max(1, symbol_count)),
                    "rank_ic": float(rank_ic),
                    "quantile_returns": {
                        str(int(bucket)): float(grouped.loc[bucket]) for bucket in grouped.index
                    },
                    "long_short": (
                        float(grouped.loc[int(quantiles)] - grouped.loc[1])
                        if 1 in grouped.index and int(quantiles) in grouped.index else None
                    ),
                    "top_turnover": float(turnover) if turnover is not None else None,
                }
            )

    primary = rows_by_horizon[1]
    rank_ics = pd.Series(
        [row["rank_ic"] for row in primary],
        index=pd.to_datetime([row["entry_date"] for row in primary]),
        dtype=float,
    )
    long_short = pd.Series(
        [row["long_short"] for row in primary],
        index=pd.to_datetime([row["entry_date"] for row in primary]),
        dtype=float,
    )
    periods_per_year = {"daily": 252, "weekly": 52, "monthly": 12}[frequency]
    bootstrap = moving_block_mean_interval(
        rank_ics,
        draws=2_000,
        minimum_observations=6,
    )
    warnings = _warnings(primary, rank_ics, bootstrap)
    if not all(isinstance(snapshot.get("input_audit"), Mapping) for snapshot in ordered_snapshots):
        warnings.append("Input cutoff audit is absent for some snapshots; point-in-time inputs are unverified")
    if any(row["missing_forward_count"] for row in primary):
        warnings.append("Quantile membership is frozen at signal time; returns use available pairs and may be biased by missing exits or delistings")
    status = "sufficient" if len(primary) >= 24 else "insufficient"
    split = max(1, len(primary) - int(np.ceil(len(primary) * float(validation_fraction))))
    development = primary[:split] if primary else []
    validation = primary[split:] if primary else []
    return {
        "status": status,
        "frequency": frequency,
        "quantiles": int(quantiles),
        "horizons": list(normalized_horizons),
        "periods": len(primary),
        "summary": {
            "mean_rank_ic": _finite(rank_ics.mean()) if not rank_ics.empty else None,
            "rank_ic_std": _finite(rank_ics.std(ddof=1)) if len(rank_ics) > 1 else None,
            "icir": (
                _finite(rank_ics.mean() / rank_ics.std(ddof=1) * np.sqrt(periods_per_year))
                if len(rank_ics) > 1 and rank_ics.std(ddof=1) > 0
                else None
            ),
            "newey_west_t_stat": (
                _finite(newey_west_mean_t_stat(rank_ics)) if len(rank_ics) > 1 else None
            ),
            "positive_ic_ratio": (
                _finite(rank_ics.gt(0).mean()) if not rank_ics.empty else None
            ),
            "average_coverage": _optional_mean(primary, "coverage"),
            "average_top_turnover": _optional_mean(primary, "top_turnover"),
            "quantile_returns": _quantile_summary(primary, int(quantiles)),
            "bootstrap_mean_ic_95": bootstrap,
            "long_short": PerformanceMetrics.summarize(
                long_short, periods_per_year=periods_per_year
            ),
        },
        "decay": {
            str(horizon): _decay_summary(rows_by_horizon[horizon])
            for horizon in normalized_horizons
        },
        "stability": {
            "validation_fraction": float(validation_fraction),
            "split_date": validation[0]["signal_date"] if validation else None,
            "development": _subperiod_summary(development, periods_per_year),
            "validation": _subperiod_summary(validation, periods_per_year),
        },
        "point_in_time_audit": _point_in_time_audit(ordered_snapshots, rows_by_horizon),
        "rows": primary,
        "warnings": warnings,
    }


def _next_session(sessions: pd.DatetimeIndex, signal_date: pd.Timestamp) -> pd.Timestamp | None:
    position = int(sessions.searchsorted(signal_date, side="right"))
    return pd.Timestamp(sessions[position]) if position < len(sessions) else None


def _snapshot_values(values: Any) -> tuple[pd.Series, int]:
    rows = values if isinstance(values, list) else []
    mapped: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        if symbol and np.isfinite(value):
            mapped[symbol] = value
    return pd.Series(mapped, dtype=float), len(rows)


def _decay_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = pd.Series([row["rank_ic"] for row in rows], dtype=float)
    return {
        "observations": len(rows),
        "mean_rank_ic": _finite(values.mean()) if not values.empty else None,
        "newey_west_t_stat": (
            _finite(newey_west_mean_t_stat(values)) if len(values) > 1 else None
        ),
    }


def _quantile_summary(rows: list[dict[str, Any]], quantiles: int) -> dict[str, float | None]:
    """Average each portfolio bucket without replacing missing periods with zero."""

    return {
        str(bucket): _finite(
            np.mean(
                [
                    row["quantile_returns"][str(bucket)]
                    for row in rows
                    if str(bucket) in row["quantile_returns"]
                ]
            )
        )
        if any(str(bucket) in row["quantile_returns"] for row in rows)
        else None
        for bucket in range(1, quantiles + 1)
    }


def _point_in_time_audit(
    snapshots: list[dict[str, Any]],
    rows_by_horizon: Mapping[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Summarize observed input cutoffs and strictly later return windows."""

    fields = (
        "factor_input_max_date",
        "security_snapshot_max_date",
        "fundamental_available_max_date",
    )
    violations: list[dict[str, str]] = []
    observed = 0
    for snapshot in snapshots:
        signal = pd.Timestamp(snapshot.get("date")).normalize()
        audit = snapshot.get("input_audit")
        if not isinstance(audit, Mapping):
            continue
        observed += 1
        for field in fields:
            value = audit.get(field)
            if value is not None and pd.Timestamp(value).normalize() > signal:
                violations.append(
                    {
                        "signal_date": signal.strftime("%Y-%m-%d"),
                        "field": field,
                        "observed_date": pd.Timestamp(value).strftime("%Y-%m-%d"),
                    }
                )
    latest = snapshots[-1].get("input_audit", {}) if snapshots else {}
    cutoff_check = False if violations else (True if observed == len(snapshots) and observed else None)
    return {
        "context_as_of_enforced": cutoff_check,
        "observed_signal_cutoffs": observed,
        "input_cutoff_violations": violations[:20],
        "factor_observation_at_or_before_signal": cutoff_check,
        "instrument_and_fundamental_as_of_enforced": cutoff_check,
        "latest_input_cutoffs": dict(latest) if isinstance(latest, Mapping) else {},
        "forward_price_field": "open",
        "forward_window_strictly_after_signal": all(
            pd.Timestamp(row["entry_date"]) > pd.Timestamp(row["signal_date"])
            for values in rows_by_horizon.values()
            for row in values
        ),
    }


def _subperiod_summary(rows: list[dict[str, Any]], periods_per_year: int) -> dict[str, Any]:
    rank_ic = pd.Series([row["rank_ic"] for row in rows], dtype=float)
    spread = pd.Series([row["long_short"] for row in rows], dtype=float)
    return {
        "periods": len(rows),
        "mean_rank_ic": _finite(rank_ic.mean()) if not rank_ic.empty else None,
        "positive_ic_ratio": _finite(rank_ic.gt(0).mean()) if not rank_ic.empty else None,
        "long_short": PerformanceMetrics.summarize(spread, periods_per_year),
    }


def _warnings(
    rows: list[dict[str, Any]], values: pd.Series, bootstrap: Mapping[str, Any]
) -> list[str]:
    warnings: list[str] = []
    if len(rows) < 24:
        warnings.append("Fewer than 24 valid factor periods; holdout evidence is insufficient")
    average_coverage = _optional_mean(rows, "coverage")
    if average_coverage is not None and average_coverage < 0.60:
        warnings.append("Average factor/forward-return coverage is below 60%")
    if not values.empty and abs(float(values.mean())) > 0.10:
        warnings.append("Absolute mean Rank IC exceeds 0.10; review alignment and leakage")
    if bootstrap.get("lower") is None or float(bootstrap["lower"]) <= 0:
        warnings.append("The 95% bootstrap interval for mean Rank IC is not strictly positive")
    return warnings


def _optional_mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return _finite(np.mean(values)) if values else None


def _finite(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


__all__ = ["factor_research_report"]
