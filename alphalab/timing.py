"""Market-timing signal generation and backtesting."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from alphalab.dataio import DataEngine, MissingDataError
from alphalab.strategy.timing import TimingStrategyConfig
from alphalab.strategy.python_runtime import execute_python_strategy
from alphalab.strategy.stages import (
    configured_timing_execution,
    configured_timing_portfolio,
    configured_timing_risk,
    configured_timing_signal,
)


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
    lookback = int(config.metadata.get("python_lookback_periods", 24) or 24)
    lookback = min(max(lookback, 2), 240)
    contexts = [_timing_context(config, returns, date, lookback) for date in returns.index]
    python_runtime: dict[str, dict] = {}
    if config.pipeline.signal.kind == "python":
        values, runtime = _execute_timing_stage(
            config.pipeline.signal, python_source, contexts
        )
        python_runtime["signal"] = runtime
        combined = pd.Series(
            [_timing_score_output(value) for value in values],
            index=returns.index,
            dtype=float,
        )
        frame = combined.rename("python_signal").to_frame()
    else:
        values = [configured_timing_signal(context) for context in contexts]
        combined = pd.Series(
            [_timing_score_output(value) for value in values],
            index=returns.index,
            dtype=float,
        )
        frame = pd.DataFrame(
            [value.get("components", {}) for value in values],
            index=returns.index,
        ).fillna(0.0)
    combined = combined.clip(lower=0.0, upper=1.0).rename("combined_score")
    portfolio_contexts = [
        {**context, "signal_score": float(combined.loc[date])}
        for context, date in zip(contexts, returns.index)
    ]
    if config.pipeline.portfolio.kind == "python":
        values, runtime = _execute_timing_stage(
            config.pipeline.portfolio, python_source, portfolio_contexts
        )
        python_runtime["portfolio"] = runtime
        proposed = pd.Series(
            [_timing_exposure_output(value, "portfolio") for value in values],
            index=returns.index,
            dtype=float,
        )
    else:
        proposed = pd.Series(
            [
                _timing_exposure_output(
                    configured_timing_portfolio(context),
                    "portfolio",
                )
                for context in portfolio_contexts
            ],
            index=returns.index,
            dtype=float,
        )
    risk_contexts = [
        {**context, "proposed_exposure": float(proposed.loc[date])}
        for context, date in zip(portfolio_contexts, returns.index)
    ]
    if config.pipeline.risk.kind == "python":
        values, runtime = _execute_timing_stage(
            config.pipeline.risk, python_source, risk_contexts
        )
        python_runtime["risk"] = runtime
        exposure = pd.Series(
            [_timing_exposure_output(value, "risk") for value in values],
            index=returns.index,
            dtype=float,
        )
    else:
        exposure = pd.Series(
            [
                _timing_exposure_output(configured_timing_risk(context), "risk")
                for context in risk_contexts
            ],
            index=returns.index,
            dtype=float,
        )
    _validate_timing_exposure(config, exposure)
    result = pd.concat([frame, combined, exposure.rename("exposure")], axis=1)
    result.attrs["python"] = python_runtime
    return result


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
    windows = (
        [max((signal.window for signal in config.signals), default=12)]
        if config.pipeline.signal.kind == "configured"
        else []
    )
    if any(
        getattr(config.pipeline, name).kind == "python"
        for name in ("signal", "portfolio", "risk")
    ):
        windows.append(
            min(max(int(config.metadata.get("python_lookback_periods", 24) or 24), 2), 240)
        )
    maximum_window = max(windows, default=12)
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
    execution_settings, execution_runtime = _timing_execution_settings(
        config,
        python_source,
        exposure,
    )
    cost_rates = pd.Series(
        {
            date: (settings["cost_bps"] + settings["slippage_bps"]) / 10_000.0
            for date, settings in execution_settings.items()
        },
        dtype=float,
    ).reindex(exposure.index)
    costs = turnover * cost_rates
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
                "execution_settings": execution_settings[date],
                "pipeline_execution": (
                    {"entrypoint": config.pipeline.execution.entrypoint}
                    if execution_runtime
                    else None
                ),
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
            "pipeline": config.pipeline.to_dict(),
            **(
                {"python": {**(python_runtime or {}), **({"execution": execution_runtime} if execution_runtime else {})}}
                if python_runtime or execution_runtime
                else {}
            ),
        },
    )


def _timing_context(
    config: TimingStrategyConfig,
    returns: pd.Series,
    date: object,
    lookback: int,
) -> dict:
    history = returns.loc[:date].tail(lookback)
    return {
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
        "signals": [asdict(signal) for signal in config.signals],
        "metadata": config.metadata,
    }


def _execute_timing_stage(stage, python_source: str | None, contexts: list[dict]) -> tuple[list, dict]:
    if python_source is None:
        raise ValueError("python_source is required when a pipeline stage uses Python")
    execution = execute_python_strategy(
        python_source,
        stage.entrypoint,
        contexts,
        stage.timeout_seconds,
    )
    return execution.values, {
        "entrypoint": stage.entrypoint,
        "source_sha256": execution.source_sha256,
        "duration_seconds": execution.duration_seconds,
        "stdout": execution.stdout,
        "stderr": execution.stderr,
    }


def _timing_score_output(value: object) -> float:
    if not isinstance(value, dict) or "score" not in value:
        raise ValueError("Python timing signal stage must return {'score': number}")
    try:
        score = float(value["score"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Python timing signal stage returned a non-numeric score") from exc
    if not np.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("Python timing signal score must be finite and in [0, 1]")
    return score


def _timing_exposure_output(value: object, stage: str) -> float:
    if not isinstance(value, dict) or "market_exposure" not in value:
        raise ValueError(
            f"Python timing {stage} stage must return {{'market_exposure': number}}"
        )
    try:
        exposure = float(value["market_exposure"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Python timing {stage} stage returned a non-numeric exposure") from exc
    if not np.isfinite(exposure):
        raise ValueError(f"Python timing {stage} exposure must be finite")
    return exposure


def _validate_timing_exposure(config: TimingStrategyConfig, exposure: pd.Series) -> None:
    if not np.isfinite(exposure.to_numpy(dtype=float)).all():
        raise ValueError("Risk-stage market exposure must be finite")
    if (
        (exposure < config.position.min_exposure - 1e-9)
        | (exposure > config.position.max_exposure + 1e-9)
    ).any():
        raise ValueError(
            "Risk-stage market exposure must stay within "
            f"{config.position.min_exposure:g}-{config.position.max_exposure:g}"
        )


def _timing_execution_settings(
    config: TimingStrategyConfig,
    python_source: str | None,
    exposure: pd.Series,
) -> tuple[dict[object, dict[str, float]], dict | None]:
    configured = asdict(config.execution)
    previous = exposure.shift(1).fillna(0.0)
    contexts = [
        {
            "strategy_type": "market_timing",
            "strategy_id": config.name,
            "entry_date": pd.Timestamp(date).strftime("%Y-%m-%d"),
            "applied_exposure": float(exposure.loc[date]),
            "previous_exposure": float(previous.loc[date]),
            "configured_execution": dict(configured),
            "metadata": config.metadata,
        }
        for date in exposure.index
    ]
    if config.pipeline.execution.kind == "configured":
        values = [configured_timing_execution(context) for context in contexts]
        runtime = None
    else:
        values, runtime = _execute_timing_stage(
            config.pipeline.execution,
            python_source,
            contexts,
        )
    settings: dict[object, dict[str, float]] = {}
    allowed = {"cost_bps", "slippage_bps"}
    for date, value in zip(exposure.index, values):
        if not isinstance(value, dict) or not isinstance(value.get("execution"), dict):
            raise ValueError("Python timing execution stage must return {'execution': {...}}")
        overrides = dict(value["execution"])
        unknown = sorted(set(overrides) - allowed)
        if unknown:
            raise ValueError(f"Python timing execution stage returned unsupported fields: {unknown}")
        merged = {**configured, **overrides}
        parsed: dict[str, float] = {}
        for name in allowed:
            try:
                parsed[name] = float(merged[name])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Python timing execution {name} must be numeric") from exc
            if not np.isfinite(parsed[name]) or parsed[name] < 0:
                raise ValueError(f"Python timing execution {name} must be finite and non-negative")
        settings[date] = parsed
    return settings, runtime


__all__ = [
    "TimingBacktestResult",
    "evaluate_timing_signals",
    "run_timing_backtest",
]
