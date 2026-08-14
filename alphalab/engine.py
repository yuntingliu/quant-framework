"""Signal generation and backtesting parity point."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd

from alphalab.dataio import DataEngine, MissingDataError, create_default_engine
from alphalab.factors.cross_sectional import (
    compute_cross_sectional_factor,
    required_fundamental_fields,
)
from alphalab.strategy.config import FactorSpec, StrategyConfig

ConfigOrPath = Union[StrategyConfig, str, Path]


@dataclass(frozen=True)
class BacktestResult:
    returns: pd.Series
    weights: pd.DataFrame
    executions: tuple[dict, ...]
    diagnostics: dict


def _load_config(config: ConfigOrPath) -> StrategyConfig:
    if isinstance(config, StrategyConfig):
        return config
    return StrategyConfig.from_yaml(config)


def _normalize_weights(weights: pd.Series, max_weight: float) -> dict[str, float]:
    """Normalize positive scores without ever exceeding the per-name cap.

    If the selected names cannot absorb 100% exposure under the cap, the
    unallocated remainder is deliberately held as cash.
    """

    if weights.empty:
        return {}
    positive = pd.to_numeric(weights, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    positive = positive.clip(lower=0.0)
    total = float(positive.sum())
    if total <= 0:
        return {}
    remaining = 1.0
    active = set(positive.index)
    result = pd.Series(0.0, index=positive.index, dtype=float)
    while active and remaining > 1e-12:
        active_index = list(active)
        active_values = positive.loc[active_index]
        active_total = float(active_values.sum())
        if active_total <= 0:
            break
        proposed = active_values / active_total * remaining
        capped = proposed[proposed > max_weight + 1e-12]
        if capped.empty:
            result.loc[active_index] = proposed
            remaining = 0.0
            break
        for symbol in capped.index:
            result.loc[symbol] = max_weight
            remaining -= max_weight
            active.remove(symbol)
        if len(active) * max_weight <= remaining + 1e-12:
            for symbol in active:
                result.loc[symbol] = max_weight
            remaining -= len(active) * max_weight
            break
    return {
        str(symbol): float(weight)
        for symbol, weight in result.items()
        if weight > 1e-12
    }


class SignalEngine:
    """Generate target weights from a strategy config and a data engine."""

    def __init__(self, data_engine: DataEngine):
        self._data = data_engine
        self._diagnostics: dict = {}

    @property
    def diagnostics(self) -> dict:
        return dict(self._diagnostics)

    def generate_targets(
        self,
        config: StrategyConfig,
        as_of_date: str,
        lookback_days: int = 120,
        auto_latest: bool = True,
    ) -> dict[str, float]:
        as_of = pd.Timestamp(as_of_date)
        if auto_latest:
            latest = self._data.get_latest_date()
            if latest and pd.Timestamp(latest) < as_of:
                as_of = pd.Timestamp(latest)

        symbols = self._resolve_symbols(config)
        instruments = self._data.get_instruments(as_of.strftime("%Y-%m-%d"))
        instrument_snapshot = None
        if not instruments.empty:
            eligible_instruments = set(instruments["symbol"].astype(str).str.upper())
            symbols = [symbol for symbol in symbols if symbol in eligible_instruments]
            if "snapshot_date" in instruments and instruments["snapshot_date"].notna().any():
                instrument_snapshot = pd.Timestamp(instruments["snapshot_date"].max()).strftime("%Y-%m-%d")
        self._diagnostics = {
            "strategy": config.name,
            "as_of_date": as_of.strftime("%Y-%m-%d"),
            "universe_size": len(symbols),
            "factor_count": len(config.factors),
            "selected_count": 0,
            "selected_symbols": [],
            "instrument_filter_applied": not instruments.empty,
            "instrument_snapshot": instrument_snapshot,
            "instrument_snapshot_future": bool(
                instrument_snapshot
                and pd.Timestamp(instrument_snapshot) > as_of
            ),
        }
        if not symbols or not config.factors:
            return {}

        start = (as_of - pd.Timedelta(days=max(lookback_days * 2, lookback_days + 30))).strftime("%Y-%m-%d")
        end = as_of.strftime("%Y-%m-%d")
        bars = self._data.get_bars(symbols, start, end, strict=False)
        if bars.empty:
            return {}
        bars = bars.copy()
        bars["date"] = pd.to_datetime(bars["date"])
        data_by_symbol = {
            symbol: frame.sort_values("date").reset_index(drop=True)
            for symbol, frame in bars.groupby("symbol")
        }
        data_by_symbol, exclusions = self._eligible_data(
            config,
            data_by_symbol,
            as_of,
        )
        eligible_symbols = sorted(data_by_symbol)
        self._diagnostics.update(
            {
                "eligible_count": len(eligible_symbols),
                "excluded_count": len(symbols) - len(eligible_symbols),
                "exclusions": exclusions,
            }
        )
        if not eligible_symbols:
            return {}
        scores = self._factor_scores(config, eligible_symbols, data_by_symbol, end)
        if scores.empty:
            return {}

        coverage = scores.notna().mean(axis=1)
        scores = scores.loc[coverage >= config.selection.min_factor_coverage]
        if scores.empty:
            return {}
        composite = scores.sum(axis=1).sort_values(ascending=False)
        selected = composite.head(config.selection.n_stocks)
        equal = pd.Series(1.0, index=selected.index, dtype=float)
        weights = _normalize_weights(equal, config.portfolio.max_weight)
        self._diagnostics.update(
            {
                "score_count": int(len(composite)),
                "selected_count": int(len(weights)),
                "selected_symbols": list(weights),
                "weight_sum": float(sum(weights.values())),
                "cash_weight": float(max(0.0, 1.0 - sum(weights.values()))),
            }
        )
        return weights

    @staticmethod
    def _eligible_data(
        config: StrategyConfig,
        data_by_symbol: dict[str, pd.DataFrame],
        as_of: pd.Timestamp,
    ) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
        eligible: dict[str, pd.DataFrame] = {}
        exclusions: dict[str, int] = {}

        def exclude(reason: str) -> None:
            exclusions[reason] = exclusions.get(reason, 0) + 1

        for symbol, frame in data_by_symbol.items():
            history = frame.loc[pd.to_datetime(frame["date"]).le(as_of)].copy()
            if len(history) < config.universe.min_history_days:
                exclude("insufficient_history")
                continue
            latest = history.iloc[-1]
            latest_date = pd.Timestamp(latest["date"])
            if (as_of - latest_date).days > config.universe.max_stale_days:
                exclude("stale_price")
                continue
            close = pd.to_numeric(pd.Series([latest.get("close")]), errors="coerce").iloc[0]
            if pd.isna(close) or float(close) < config.universe.min_price:
                exclude("minimum_price")
                continue
            if config.universe.require_positive_volume:
                volume = pd.to_numeric(pd.Series([latest.get("volume")]), errors="coerce").iloc[0]
                if pd.isna(volume) or float(volume) <= 0:
                    exclude("not_trading")
                    continue
            if config.universe.min_average_amount > 0:
                if "amount" not in history:
                    exclude("missing_amount")
                    continue
                amount = pd.to_numeric(history["amount"], errors="coerce").tail(20).mean()
                if pd.isna(amount) or float(amount) < config.universe.min_average_amount:
                    exclude("minimum_liquidity")
                    continue
            eligible[symbol] = history
        return eligible, exclusions

    def _resolve_symbols(self, config: StrategyConfig) -> list[str]:
        if config.universe.symbols:
            return list(config.universe.symbols)
        return self._data.get_symbols(config.universe.pool)

    def _factor_scores(
        self,
        config: StrategyConfig,
        symbols: list[str],
        data_by_symbol: dict[str, pd.DataFrame],
        as_of_date: str,
    ) -> pd.DataFrame:
        columns: list[pd.Series] = []
        fundamental_names = required_fundamental_fields(config.factors)
        fundamentals = pd.DataFrame()
        if fundamental_names:
            fundamentals = self._load_latest_fundamentals(symbols, fundamental_names, as_of_date)

        for factor in config.factors:
            raw = compute_cross_sectional_factor(factor, data_by_symbol, fundamentals)
            ranked = self._rank_factor(raw.reindex(symbols), factor)
            if not ranked.empty:
                columns.append(ranked.rename(factor.name))
        if not columns:
            return pd.DataFrame()
        return pd.concat(columns, axis=1)

    def _load_latest_fundamentals(self, symbols: list[str], fields: list[str], as_of_date: str) -> pd.DataFrame:
        year = pd.Timestamp(as_of_date).year
        try:
            return self._data.get_fundamentals(
                symbols,
                fields,
                start_quarter=f"{year - 3}q1",
                end_quarter=f"{year}q4",
                asof_date=as_of_date,
                strict=False,
            )
        except MissingDataError:
            return pd.DataFrame(columns=["quarter", "symbol", *fields])

    @staticmethod
    def _rank_factor(values: pd.Series, factor: FactorSpec) -> pd.Series:
        values = pd.to_numeric(values, errors="coerce").dropna()
        if values.empty:
            return pd.Series(dtype=float, name=factor.name)
        ranks = values.rank(pct=True)
        if factor.direction == "short":
            ranks = 1.0 - ranks
        return ranks * factor.weight


def run_backtest(
    config: ConfigOrPath,
    start_date: str,
    end_date: str,
    data_engine: DataEngine | None = None,
    lookback_days: int = 120,
) -> tuple[pd.Series, pd.DataFrame]:
    """Run the configured backtest and return its net returns and holdings."""

    result = run_backtest_detailed(
        config,
        start_date,
        end_date,
        data_engine=data_engine,
        lookback_days=lookback_days,
    )
    return result.returns, result.weights


def run_backtest_detailed(
    config: ConfigOrPath,
    start_date: str,
    end_date: str,
    data_engine: DataEngine | None = None,
    lookback_days: int = 120,
) -> BacktestResult:
    """Run a PIT rebalance simulation with next-session execution constraints.

    Signals are formed after period-end closes. Trades occur at the configured
    price on the next observed market session. Cash, one-way costs, slippage,
    market impact, positive-volume checks, and daily amount participation limits
    are represented explicitly in each execution record.
    """

    cfg = _load_config(config)
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    if start >= end:
        raise ValueError("start_date must be before end_date")
    engine = SignalEngine(data_engine or create_default_engine())
    symbols = engine._resolve_symbols(cfg)
    if not symbols:
        return _empty_backtest(cfg, "empty universe")

    warmup_start = (start - pd.Timedelta(days=lookback_days * 2)).strftime("%Y-%m-%d")
    bars = engine._data.get_bars(
        symbols,
        warmup_start,
        end.strftime("%Y-%m-%d"),
        strict=False,
        fields=["open", "close", "volume", "amount"],
        use_cache=False,
    )
    if bars.empty:
        return _empty_backtest(cfg, "no market bars")
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"])
    schedule = _rebalance_schedule(bars["date"], start, end, cfg.portfolio.rebalance_freq)
    if len(schedule) < 2:
        return _empty_backtest(cfg, "insufficient rebalance periods")

    returns: dict[pd.Timestamp, float] = {}
    weights_by_date: dict[pd.Timestamp, dict[str, float]] = {}
    executions: list[dict] = []
    previous: dict[str, float] = {}
    warnings: set[str] = set()
    for index, (signal_date, entry_date) in enumerate(schedule[:-1]):
        _, exit_date = schedule[index + 1]
        target = engine.generate_targets(
            cfg,
            signal_date.strftime("%Y-%m-%d"),
            lookback_days=lookback_days,
            auto_latest=False,
        )
        if not target:
            target = dict(previous)
            warnings.add("empty signal retained the prior portfolio")
        signal_diagnostics = engine.diagnostics
        if not signal_diagnostics.get("instrument_filter_applied"):
            warnings.add(
                "PIT instrument snapshots unavailable; bar-history universe was used"
            )
        if signal_diagnostics.get("instrument_snapshot_future"):
            warnings.add(
                "instrument metadata snapshot post-dates at least one signal date"
            )
        executed, execution = _apply_execution_constraints(
            target,
            previous,
            bars,
            entry_date,
            cfg,
        )
        asset_returns = _asset_period_returns(
            bars,
            executed,
            entry_date,
            exit_date,
            cfg.execution.execution_price,
        )
        cash_before_cost = max(0.0, 1.0 - sum(executed.values()))
        cash_after_cost = cash_before_cost - execution["total_cost"]
        if cash_after_cost < -1e-9:
            warnings.add("execution costs exceeded available cash")
        ending_values = {
            symbol: weight * (1.0 + asset_returns.get(symbol, 0.0))
            for symbol, weight in executed.items()
        }
        ending_nav = cash_after_cost + sum(ending_values.values())
        if not np.isfinite(ending_nav) or ending_nav <= 0:
            warnings.add("portfolio NAV became invalid")
            continue
        returns[entry_date] = float(ending_nav - 1.0)
        weights_by_date[entry_date] = dict(executed)
        previous = {
            symbol: float(value / ending_nav)
            for symbol, value in ending_values.items()
            if value > 1e-12
        }
        executions.append(
            {
                **execution,
                "signal_date": signal_date.strftime("%Y-%m-%d"),
                "entry_date": entry_date.strftime("%Y-%m-%d"),
                "exit_date": exit_date.strftime("%Y-%m-%d"),
                "execution_price": cfg.execution.execution_price,
                "gross_return": float(
                    sum(executed.get(symbol, 0.0) * value for symbol, value in asset_returns.items())
                ),
                "net_return": float(ending_nav - 1.0),
                "cash_weight": float(cash_before_cost),
                "universe": {
                    key: signal_diagnostics.get(key)
                    for key in (
                        "as_of_date",
                        "universe_size",
                        "eligible_count",
                        "excluded_count",
                        "exclusions",
                        "instrument_filter_applied",
                        "instrument_snapshot",
                        "instrument_snapshot_future",
                    )
                },
            }
        )

    returns_series = pd.Series(returns, name=cfg.name, dtype=float).dropna()
    weights_df = pd.DataFrame.from_dict(weights_by_date, orient="index").fillna(0.0).sort_index()
    return BacktestResult(
        returns=returns_series,
        weights=weights_df,
        executions=tuple(executions),
        diagnostics={
            "frequency": cfg.portfolio.rebalance_freq,
            "execution": asdict(cfg.execution),
            "periods": len(returns_series),
            "warnings": sorted(warnings),
        },
    )


def _rebalance_schedule(
    dates: pd.Series,
    start: pd.Timestamp,
    end: pd.Timestamp,
    frequency: str,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    sessions = pd.DatetimeIndex(pd.to_datetime(dates).dropna().unique()).sort_values()
    sessions = sessions[sessions <= end]
    if sessions.empty:
        return []
    if frequency == "weekly":
        signals = pd.Series(sessions, index=sessions).groupby(sessions.to_period("W-FRI")).max()
    else:
        signals = pd.Series(sessions, index=sessions).groupby(sessions.to_period("M")).max()
    schedule: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for raw_signal in signals:
        signal = pd.Timestamp(raw_signal)
        if signal < start or signal >= end:
            continue
        position = int(sessions.searchsorted(signal, side="right"))
        if position >= len(sessions):
            continue
        entry = pd.Timestamp(sessions[position])
        if entry <= end:
            schedule.append((signal, entry))
    return schedule


def _apply_execution_constraints(
    target: dict[str, float],
    current: dict[str, float],
    bars: pd.DataFrame,
    entry_date: pd.Timestamp,
    config: StrategyConfig,
) -> tuple[dict[str, float], dict]:
    entry_rows = (
        bars.loc[bars["date"].eq(entry_date)]
        .drop_duplicates("symbol", keep="last")
        .set_index("symbol")
    )
    field = "open" if config.execution.execution_price == "next_open" else "close"
    desired: dict[str, float] = {}
    constrained: set[str] = set()
    missing_amount: set[str] = set()
    participation: dict[str, float] = {}
    universe = set(target) | set(current)
    for symbol in universe:
        old = float(current.get(symbol, 0.0))
        wanted = float(target.get(symbol, 0.0))
        row = entry_rows.loc[symbol] if symbol in entry_rows.index else None
        price = pd.to_numeric(pd.Series([row.get(field) if row is not None else None]), errors="coerce").iloc[0]
        volume = pd.to_numeric(pd.Series([row.get("volume") if row is not None else None]), errors="coerce").iloc[0]
        if pd.isna(price) or float(price) <= 0 or pd.isna(volume) or float(volume) <= 0:
            desired[symbol] = old
            if abs(wanted - old) > 1e-12:
                constrained.add(symbol)
            continue
        delta = wanted - old
        amount = pd.to_numeric(pd.Series([row.get("amount") if row is not None else None]), errors="coerce").iloc[0]
        if pd.notna(amount) and float(amount) > 0:
            capacity = (
                float(amount)
                * config.execution.max_participation_rate
                / config.execution.portfolio_value
            )
            if abs(delta) > capacity:
                delta = float(np.sign(delta) * capacity)
                constrained.add(symbol)
            participation[symbol] = min(
                1.0,
                abs(delta) * config.execution.portfolio_value / float(amount),
            )
        else:
            missing_amount.add(symbol)
            participation[symbol] = 0.0
            desired[symbol] = old
            if abs(delta) > 1e-12:
                constrained.add(symbol)
            continue
        desired[symbol] = max(0.0, old + delta)

    executed = dict(current)
    for symbol in universe:
        old = float(executed.get(symbol, 0.0))
        wanted = float(desired.get(symbol, 0.0))
        if wanted < old:
            executed[symbol] = wanted
    sell_deltas = {
        symbol: executed.get(symbol, 0.0) - current.get(symbol, 0.0)
        for symbol in set(executed) | set(current)
        if executed.get(symbol, 0.0) < current.get(symbol, 0.0)
    }
    sell_cost = _execution_cost(sell_deltas, participation, config)["total_cost"]
    available_cash = max(0.0, 1.0 - sum(executed.values()) - sell_cost)
    for symbol in sorted(universe, key=lambda value: desired.get(value, 0.0) - current.get(value, 0.0), reverse=True):
        old = float(executed.get(symbol, 0.0))
        wanted = float(desired.get(symbol, 0.0))
        if wanted <= old:
            continue
        marginal_cost = _execution_cost_rate(symbol, participation, config)
        increase = min(wanted - old, available_cash / (1.0 + marginal_cost))
        executed[symbol] = old + increase
        available_cash -= increase * (1.0 + marginal_cost)
        if increase + 1e-12 < wanted - old:
            constrained.add(symbol)
        if available_cash <= 1e-12:
            break
    executed = {
        symbol: float(weight)
        for symbol, weight in executed.items()
        if weight > 1e-12
    }
    deltas = {
        symbol: executed.get(symbol, 0.0) - current.get(symbol, 0.0)
        for symbol in set(executed) | set(current)
    }
    traded_weight = float(sum(abs(value) for value in deltas.values()))
    previous_cash = max(0.0, 1.0 - sum(current.values()))
    next_cash = max(0.0, 1.0 - sum(executed.values()))
    turnover = 0.5 * (traded_weight + abs(next_cash - previous_cash))
    costs = _execution_cost(deltas, participation, config)
    fixed_cost = costs["fixed_cost"]
    impact_cost = costs["impact_cost"]
    return executed, {
        "turnover": float(turnover),
        "traded_weight": traded_weight,
        "fixed_cost": float(fixed_cost),
        "impact_cost": float(impact_cost),
        "total_cost": costs["total_cost"],
        "target_count": len(target),
        "executed_count": len(executed),
        "constrained_symbols": sorted(constrained),
        "missing_amount_symbols": sorted(missing_amount),
    }


def _execution_cost_rate(
    symbol: str,
    participation: dict[str, float],
    config: StrategyConfig,
) -> float:
    fixed = (
        config.execution.cost_bps + config.execution.slippage_bps
    ) / 10_000.0
    impact = (config.execution.impact_bps / 10_000.0) * np.sqrt(
        min(
            1.0,
            participation.get(symbol, 0.0)
            / config.execution.max_participation_rate,
        )
    )
    return float(fixed + impact)


def _execution_cost(
    deltas: dict[str, float],
    participation: dict[str, float],
    config: StrategyConfig,
) -> dict[str, float]:
    fixed_rate = (
        config.execution.cost_bps + config.execution.slippage_bps
    ) / 10_000.0
    fixed_cost = sum(abs(delta) for delta in deltas.values()) * fixed_rate
    impact_cost = sum(
        abs(delta)
        * max(0.0, _execution_cost_rate(symbol, participation, config) - fixed_rate)
        for symbol, delta in deltas.items()
    )
    return {
        "fixed_cost": float(fixed_cost),
        "impact_cost": float(impact_cost),
        "total_cost": float(fixed_cost + impact_cost),
    }


def _asset_period_returns(
    bars: pd.DataFrame,
    weights: dict[str, float],
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    execution_price: str,
) -> dict[str, float]:
    if not weights:
        return {}
    field = "open" if execution_price == "next_open" else "close"
    selected = bars.loc[bars["symbol"].isin(weights)].copy()
    result: dict[str, float] = {}
    for symbol, frame in selected.groupby("symbol"):
        ordered = frame.sort_values("date")
        entry_rows = ordered.loc[ordered["date"].le(entry_date)]
        exit_rows = ordered.loc[ordered["date"].le(exit_date)]
        if entry_rows.empty or exit_rows.empty:
            result[str(symbol)] = 0.0
            continue
        entry_row = entry_rows.iloc[-1]
        exit_row = exit_rows.iloc[-1]
        entry_value = entry_row.get(field) if pd.Timestamp(entry_row["date"]) == entry_date else entry_row.get("close")
        exit_value = exit_row.get(field) if pd.Timestamp(exit_row["date"]) == exit_date else exit_row.get("close")
        entry_price = pd.to_numeric(pd.Series([entry_value]), errors="coerce").iloc[0]
        exit_price = pd.to_numeric(pd.Series([exit_value]), errors="coerce").iloc[0]
        result[str(symbol)] = (
            float(exit_price / entry_price - 1.0)
            if pd.notna(entry_price) and pd.notna(exit_price) and float(entry_price) > 0
            else 0.0
        )
    return result


def _empty_backtest(config: StrategyConfig, warning: str) -> BacktestResult:
    return BacktestResult(
        returns=pd.Series(dtype=float, name=config.name),
        weights=pd.DataFrame(),
        executions=(),
        diagnostics={
            "frequency": config.portfolio.rebalance_freq,
            "execution": asdict(config.execution),
            "periods": 0,
            "warnings": [warning],
        },
    )


__all__ = ["BacktestResult", "SignalEngine", "run_backtest", "run_backtest_detailed"]
