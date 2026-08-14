"""Market-timing signal generation and backtesting."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from alphalab.dataio import DataEngine, MissingDataError
from alphalab.strategy.timing import TimingSignalSpec, TimingStrategyConfig
from alphalab.strategy.python_runtime import execute_python_strategy


@dataclass(frozen=True)
class TimingBacktestResult:
    returns: pd.Series
    benchmark: pd.Series
    exposure: pd.Series
    signal_scores: pd.DataFrame
    executions: tuple[dict, ...]
    diagnostics: dict


def evaluate_timing_signals(
    config: TimingStrategyConfig,
    market_returns: pd.Series,
    python_source: str | None = None,
) -> pd.DataFrame:
    """Evaluate registered timing signals without using future returns."""

    returns = pd.to_numeric(market_returns, errors="coerce").dropna().sort_index()
    if returns.empty:
        return pd.DataFrame(columns=[*config.signal_names, "combined_score", "exposure"])
    if config.implementation.kind == "python":
        if python_source is None:
            raise ValueError("python_source is required for a Python strategy")
        lookback = int(config.metadata.get("python_lookback_periods", 24) or 24)
        lookback = min(max(lookback, 2), 240)
        contexts = []
        for date in returns.index:
            history = returns.loc[:date].tail(lookback)
            contexts.append(
                {
                    "strategy_type": "market_timing",
                    "strategy_id": config.name,
                    "as_of_date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                    "market_factor": config.market_factor,
                    "market_returns": [
                        {
                            "date": pd.Timestamp(history_date).strftime("%Y-%m-%d"),
                            "value": float(value),
                        }
                        for history_date, value in history.items()
                    ],
                    "limits": {
                        "min_exposure": config.position.min_exposure,
                        "max_exposure": config.position.max_exposure,
                    },
                    "metadata": config.metadata,
                }
            )
        execution = execute_python_strategy(
            python_source,
            config.implementation.entrypoint,
            contexts,
            config.implementation.timeout_seconds,
        )
        exposures = []
        for value in execution.values:
            if not isinstance(value, dict) or "market_exposure" not in value:
                raise ValueError(
                    "Python market-timing strategy must return "
                    "{'market_exposure': number}"
                )
            try:
                exposure = float(value["market_exposure"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Python strategy returned a non-numeric market exposure") from exc
            if not np.isfinite(exposure):
                raise ValueError("Python strategy market exposure must be finite")
            if not (
                config.position.min_exposure - 1e-9
                <= exposure
                <= config.position.max_exposure + 1e-9
            ):
                raise ValueError(
                    "Python strategy market exposure must stay within "
                    f"{config.position.min_exposure:g}-{config.position.max_exposure:g}"
                )
            exposures.append(exposure)
        exposure_series = pd.Series(exposures, index=returns.index, name="exposure")
        width = config.position.max_exposure - config.position.min_exposure
        combined = (
            (exposure_series - config.position.min_exposure) / width
            if width > 0
            else pd.Series(0.0, index=returns.index)
        ).rename("combined_score")
        frame = pd.concat(
            [exposure_series.rename("python_exposure"), combined, exposure_series],
            axis=1,
        )
        frame.attrs["python"] = {
            "source_sha256": execution.source_sha256,
            "duration_seconds": execution.duration_seconds,
            "stdout": execution.stdout,
            "stderr": execution.stderr,
        }
        return frame
    wealth = (1.0 + returns).cumprod()
    columns: list[pd.Series] = []
    weighted: list[pd.Series] = []
    for index, signal in enumerate(config.signals):
        score = _timing_score(signal, returns, wealth)
        label = signal.kind if config.signal_names.count(signal.kind) == 1 else f"{signal.kind}_{index + 1}"
        columns.append(score.rename(label))
        weighted.append((score * signal.weight).rename(label))
    frame = pd.concat(columns, axis=1) if columns else pd.DataFrame(index=returns.index)
    if weighted and config.total_weight > 0:
        combined = pd.concat(weighted, axis=1).sum(axis=1) / config.total_weight
    else:
        combined = pd.Series(0.0, index=returns.index, dtype=float)
    combined = combined.clip(lower=0.0, upper=1.0).rename("combined_score")
    exposure = (
        config.position.min_exposure
        + combined * (config.position.max_exposure - config.position.min_exposure)
    ).rename("exposure")
    return pd.concat([frame, combined, exposure], axis=1)


def run_timing_backtest(
    config: TimingStrategyConfig,
    start_date: str,
    end_date: str,
    data_engine: DataEngine,
    python_source: str | None = None,
) -> TimingBacktestResult:
    """Run a monthly market-timing strategy with one-period-lagged exposure."""

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    if start >= end:
        raise ValueError("start_date must be before end_date")
    maximum_window = (
        min(max(int(config.metadata.get("python_lookback_periods", 24) or 24), 2), 240)
        if config.implementation.kind == "python"
        else max((signal.window for signal in config.signals), default=12)
    )
    warmup = (start - pd.DateOffset(months=maximum_window * 2 + 6)).strftime("%Y-%m-%d")
    factors = data_engine.get_factors(
        [config.market_factor],
        warmup,
        end.strftime("%Y-%m-%d"),
        freq="1M",
        strict=True,
        use_cache=False,
    )
    if factors.empty or config.market_factor not in factors:
        raise MissingDataError(f"No {config.market_factor} returns are available for timing research")
    market = pd.to_numeric(factors[config.market_factor], errors="coerce").dropna().sort_index()
    scores = evaluate_timing_signals(config, market, python_source)
    python_runtime = scores.attrs.get("python")
    evaluation = pd.concat([market.rename("market_return"), scores], axis=1).dropna(subset=["market_return"])
    evaluation = evaluation.loc[evaluation.index.to_series().between(start, end)]
    if evaluation.empty:
        raise MissingDataError("No market-factor observations are available in the requested timing range")

    exposure = evaluation["exposure"].shift(1).fillna(config.position.min_exposure).clip(0.0, 1.0)
    turnover = exposure.diff().abs().fillna(exposure.abs())
    cost_rate = (config.execution.cost_bps + config.execution.slippage_bps) / 10_000.0
    costs = turnover * cost_rate
    strategy_returns = (exposure * evaluation["market_return"] - costs).rename(config.name)
    benchmark = evaluation["market_return"].rename("benchmark")
    signal_scores = evaluation.drop(columns=["market_return"]).copy()
    executions: list[dict] = []
    prior_dates = list(evaluation.index.to_series().shift(1))
    for index, (date, applied_exposure) in enumerate(exposure.items()):
        signal_date = prior_dates[index]
        executions.append(
            {
                "signal_date": (
                    pd.Timestamp(signal_date).strftime("%Y-%m-%d")
                    if pd.notna(signal_date)
                    else None
                ),
                "entry_date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                "exit_date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                "execution_price": "monthly_factor_close",
                "turnover": float(turnover.loc[date]),
                "traded_weight": float(turnover.loc[date]),
                "fixed_cost": float(costs.loc[date]),
                "impact_cost": 0.0,
                "total_cost": float(costs.loc[date]),
                "cash_weight": float(1.0 - applied_exposure),
                "gross_return": float(applied_exposure * benchmark.loc[date]),
                "net_return": float(strategy_returns.loc[date]),
                "constrained_symbols": [],
                "missing_amount_symbols": [],
                "participation": {},
            }
        )
    return TimingBacktestResult(
        returns=strategy_returns,
        benchmark=benchmark,
        exposure=exposure.rename("MARKET_EXPOSURE"),
        signal_scores=signal_scores,
        executions=tuple(executions),
        diagnostics={
            "strategy_type": "market_timing",
            "market_factor": config.market_factor,
            "frequency": "monthly",
            "periods": int(len(strategy_returns)),
            "average_exposure": float(exposure.mean()),
            "latest_exposure": float(signal_scores["exposure"].iloc[-1]),
            "latest_signal_date": pd.Timestamp(signal_scores.index[-1]).strftime("%Y-%m-%d"),
            "turnover": float(turnover.sum()),
            **({"python": python_runtime} if python_runtime else {}),
        },
    )


def _timing_score(
    signal: TimingSignalSpec,
    returns: pd.Series,
    wealth: pd.Series,
) -> pd.Series:
    minimum = max(2, signal.window // 2)
    if signal.kind == "trend":
        moving_average = wealth.rolling(signal.window, min_periods=minimum).mean()
        relative = wealth / moving_average - 1.0
        return relative.gt(signal.threshold).astype(float).where(moving_average.notna(), 0.0)
    if signal.kind == "momentum":
        momentum = (1.0 + returns).rolling(signal.window, min_periods=minimum).apply(np.prod, raw=True) - 1.0
        return momentum.gt(signal.threshold).astype(float).where(momentum.notna(), 0.0)
    volatility = returns.rolling(signal.window, min_periods=minimum).std(ddof=1) * np.sqrt(12.0)
    return (signal.threshold / volatility.replace(0.0, np.nan)).clip(0.0, 1.0).fillna(0.0)


__all__ = [
    "TimingBacktestResult",
    "evaluate_timing_signals",
    "run_timing_backtest",
]
