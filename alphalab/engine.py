"""Signal generation and backtesting parity point."""
from __future__ import annotations

from pathlib import Path
from typing import Union

import pandas as pd

from alphalab.dataio import DataEngine, MissingDataError, create_default_engine, to_wide
from alphalab.factors.fundamental import FundamentalFactors
from alphalab.factors.technical import TechnicalFactors
from alphalab.strategy.config import FactorSpec, StrategyConfig

ConfigOrPath = Union[StrategyConfig, str, Path]


def _load_config(config: ConfigOrPath) -> StrategyConfig:
    if isinstance(config, StrategyConfig):
        return config
    return StrategyConfig.from_yaml(config)


def _normalize_weights(weights: pd.Series, max_weight: float) -> dict[str, float]:
    if weights.empty:
        return {}
    clipped = weights.clip(lower=0.0, upper=max_weight)
    total = float(clipped.sum())
    if total <= 0:
        return {}
    normalized = clipped / total
    if normalized.max() > max_weight + 1e-12:
        normalized = normalized.clip(upper=max_weight)
        normalized = normalized / normalized.sum()
    return {str(symbol): float(weight) for symbol, weight in normalized.items() if weight > 1e-12}


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
        self._diagnostics = {
            "strategy": config.name,
            "as_of_date": as_of.strftime("%Y-%m-%d"),
            "universe_size": len(symbols),
            "factor_count": len(config.factors),
            "selected_count": 0,
            "selected_symbols": [],
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
        scores = self._factor_scores(config, symbols, data_by_symbol, end)
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
            }
        )
        return weights

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
        technical = TechnicalFactors()
        fundamental = FundamentalFactors()
        fundamental_names = [factor.name for factor in config.factors if factor.source == "fundamental"]
        fundamentals = pd.DataFrame()
        if fundamental_names:
            fundamentals = self._load_latest_fundamentals(symbols, fundamental_names, as_of_date)

        for factor in config.factors:
            if factor.source == "technical":
                raw = technical.compute(factor.name, data_by_symbol)
            else:
                raw = fundamental.compute(factor.name, fundamentals)
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
    """Run a simple rebalance-to-next-period backtest.

    Signal dates are period ends. Targets generated at each signal date are
    applied to the next period return, avoiding same-period lookahead.
    """

    cfg = _load_config(config)
    engine = SignalEngine(data_engine or create_default_engine())
    symbols = engine._resolve_symbols(cfg)
    if not symbols:
        return pd.Series(dtype=float, name=cfg.name), pd.DataFrame()

    warmup_start = (pd.Timestamp(start_date) - pd.Timedelta(days=lookback_days * 2)).strftime("%Y-%m-%d")
    bars = engine._data.get_bars(symbols, warmup_start, end_date, strict=False, fields=["close"])
    if bars.empty:
        return pd.Series(dtype=float, name=cfg.name), pd.DataFrame()
    close = to_wide(bars, "close").sort_index()
    if cfg.portfolio.rebalance_freq == "weekly":
        prices = close.resample("W-FRI").last().dropna(how="all")
    else:
        prices = close.resample(pd.offsets.MonthEnd()).last().dropna(how="all")
    period_returns = prices.pct_change(fill_method=None).shift(-1)
    signal_dates = [date for date in prices.index if pd.Timestamp(start_date) <= date <= pd.Timestamp(end_date)]

    returns: dict[pd.Timestamp, float] = {}
    weights_by_date: dict[pd.Timestamp, dict[str, float]] = {}
    previous: dict[str, float] = {}
    cost_rate = cfg.execution.cost_bps / 10000.0
    for signal_date in signal_dates:
        weights = engine.generate_targets(cfg, signal_date.strftime("%Y-%m-%d"), lookback_days=lookback_days, auto_latest=False)
        if not weights:
            weights = previous
        weights_by_date[signal_date] = dict(weights)
        next_ret = period_returns.loc[signal_date] if signal_date in period_returns.index else pd.Series(dtype=float)
        gross = float(sum(float(weight) * float(next_ret.get(symbol, 0.0) or 0.0) for symbol, weight in weights.items()))
        turnover = sum(abs(weights.get(symbol, 0.0) - previous.get(symbol, 0.0)) for symbol in set(weights) | set(previous)) / 2.0
        returns[signal_date] = gross - turnover * cost_rate
        previous = dict(weights)

    returns_series = pd.Series(returns, name=cfg.name, dtype=float).dropna()
    weights_df = pd.DataFrame.from_dict(weights_by_date, orient="index").fillna(0.0).sort_index()
    return returns_series, weights_df
