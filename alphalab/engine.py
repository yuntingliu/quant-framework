"""Signal generation and backtesting parity point."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd

from alphalab.dataio import DataEngine, MissingDataError, create_default_engine
from alphalab.factors.cross_sectional import (
    compute_cross_sectional_factor,
    required_fundamental_fields,
)
from alphalab.strategy.config import ExecutionSpec, FactorSpec, StrategyConfig
from alphalab.strategy.python_runtime import execute_python_strategy, python_source_sha256
from alphalab.strategy.stages import (
    configured_stock_execution,
    configured_stock_portfolio,
    configured_stock_risk,
    configured_stock_signal,
)

ConfigOrPath = StrategyConfig


@dataclass(frozen=True)
class BacktestResult:
    returns: pd.Series
    weights: pd.DataFrame
    executions: tuple[dict, ...]
    diagnostics: dict


def _load_config(config: ConfigOrPath) -> StrategyConfig:
    if not isinstance(config, StrategyConfig):
        raise TypeError("config must be a StrategyConfig built from structured project settings")
    return config


class SignalEngine:
    """Generate target weights from a strategy config and a data engine."""

    def __init__(self, data_engine: DataEngine):
        self._data = data_engine
        self._diagnostics: dict = {}
        self._selection_snapshot: dict = {}

    @property
    def diagnostics(self) -> dict:
        return dict(self._diagnostics)

    @property
    def selection_snapshot(self) -> dict:
        """Return the latest cross-sectional ranking preview.

        The payload is deliberately separate from diagnostics so backtests can
        retain compact execution metadata while interactive research surfaces
        can explain which stocks crossed the selection cutoff and why.
        """

        return {
            **self._selection_snapshot,
            "rows": [dict(row) for row in self._selection_snapshot.get("rows", [])],
        }

    def generate_targets(
        self,
        config: StrategyConfig,
        as_of_date: str,
        lookback_days: int = 120,
        auto_latest: bool = True,
        python_source: str | None = None,
        current_weights: dict[str, float] | None = None,
        complete_python_source: str | None = None,
        stage_parameters: dict[str, dict] | None = None,
        bars_override: pd.DataFrame | None = None,
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
        self._selection_snapshot = {
            "as_of_date": as_of.strftime("%Y-%m-%d"),
            "universe_size": len(symbols),
            "eligible_count": 0,
            "scored_count": 0,
            "requested_count": config.selection.n_stocks,
            "selected_count": 0,
            "cash_weight": 1.0,
            "factor_names": list(config.factor_names),
            "exclusions": {},
            "rows": [],
        }
        if not symbols or (
            not config.factors
            and config.pipeline.signal.kind == "configured"
            and config.pipeline.portfolio.kind != "python"
        ):
            return {}

        start = (as_of - pd.Timedelta(days=max(lookback_days * 2, lookback_days + 30))).strftime("%Y-%m-%d")
        end = as_of.strftime("%Y-%m-%d")
        if bars_override is None:
            bars = self._data.get_bars(symbols, start, end, strict=False)
        else:
            supplied = bars_override.copy()
            supplied["date"] = pd.to_datetime(supplied["date"])
            bars = supplied.loc[
                supplied["symbol"].isin(symbols)
                & supplied["date"].between(pd.Timestamp(start), as_of)
            ].copy()
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
        self._selection_snapshot.update(
            {
                "eligible_count": len(eligible_symbols),
                "exclusions": dict(exclusions),
            }
        )
        if not eligible_symbols:
            return {}
        factor_scores = self._factor_scores(config, eligible_symbols, data_by_symbol, end)
        context = self._candidate_context(
            config,
            as_of,
            eligible_symbols,
            data_by_symbol,
            factor_scores,
            current_weights or {},
            lookback_days,
        )
        if complete_python_source is not None:
            return self._run_complete_pipeline(
                config,
                context,
                complete_python_source,
                stage_parameters or {},
                eligible_symbols,
                factor_scores,
                as_of,
            )
        python_runtime: dict[str, dict] = {}

        if config.pipeline.signal.kind == "python":
            value, runtime = self._run_python_stage(
                config.pipeline.signal,
                python_source,
                context,
            )
            python_runtime["signal"] = runtime
            signal_scores = self._coerce_scores(value, set(eligible_symbols))
            coverage = pd.Series(1.0, index=list(signal_scores), dtype=float)
        else:
            coverage = (
                factor_scores.notna().mean(axis=1)
                if not factor_scores.empty
                else pd.Series(1.0, index=eligible_symbols, dtype=float)
            )
            signal_scores = self._coerce_scores(
                configured_stock_signal(context),
                set(eligible_symbols),
            )
        ordered_scores = dict(
            sorted(signal_scores.items(), key=lambda item: item[1], reverse=True)
        )
        stage_context = {**context, "signal_scores": ordered_scores}

        if config.pipeline.portfolio.kind == "python":
            value, runtime = self._run_python_stage(
                config.pipeline.portfolio,
                python_source,
                stage_context,
            )
            python_runtime["portfolio"] = runtime
            proposed = self._coerce_weights(
                value,
                set(eligible_symbols),
                label="portfolio",
            )
        else:
            proposed = self._coerce_weights(
                configured_stock_portfolio(stage_context),
                set(eligible_symbols),
                label="portfolio",
            )

        risk_context = {**stage_context, "proposed_weights": dict(proposed)}
        if config.pipeline.risk.kind == "python":
            value, runtime = self._run_python_stage(
                config.pipeline.risk,
                python_source,
                risk_context,
            )
            python_runtime["risk"] = runtime
            weights = self._coerce_weights(
                value,
                set(eligible_symbols),
                label="risk",
            )
        else:
            weights = self._coerce_weights(
                configured_stock_risk(risk_context),
                set(eligible_symbols),
                label="risk",
            )
        weights = self._validate_target_weights(config, weights, set(eligible_symbols))
        self._finish_selection_snapshot(
            config,
            ordered_scores,
            factor_scores,
            coverage,
            weights,
            python_runtime,
        )
        return weights

    def _run_complete_pipeline(
        self,
        config: StrategyConfig,
        context: dict,
        source: str,
        stage_parameters: dict[str, dict],
        eligible_symbols: list[str],
        factor_scores: pd.DataFrame,
        as_of: pd.Timestamp,
    ) -> dict[str, float]:
        """Execute the frozen six-stage module once and enforce core invariants."""

        complete_context = {
            **context,
            "strategy_type": "six_stage",
            "stage_parameters": stage_parameters,
            "market_returns": self._market_returns_context(as_of),
        }
        execution = execute_python_strategy(source, "run_strategy", [complete_context], 15.0)
        value = execution.values[0]
        if not isinstance(value, dict):
            raise ValueError("run_strategy must return a mapping")
        universe = value.get("universe")
        if not isinstance(universe, dict) or not isinstance(universe.get("symbols"), list):
            raise ValueError("build_universe must return {'symbols': [...]}")
        allowed = set(eligible_symbols)
        universe_symbols = []
        for raw_symbol in universe["symbols"]:
            symbol = str(raw_symbol).strip().upper()
            if symbol not in allowed:
                raise ValueError(f"Universe stage returned an ineligible symbol: {symbol}")
            if symbol not in universe_symbols:
                universe_symbols.append(symbol)
        selection = value.get("selection")
        if not isinstance(selection, dict):
            raise ValueError("select_assets must return a mapping")
        selected = selection.get("selected")
        if not isinstance(selected, list):
            raise ValueError("select_assets must return {'selected': [...], 'scores': {...}}")
        universe_set = set(universe_symbols)
        selected_symbols = [str(item).strip().upper() for item in selected]
        unknown_selected = sorted(set(selected_symbols) - universe_set)
        if unknown_selected:
            raise ValueError(f"Selection stage returned symbols outside its universe: {unknown_selected}")
        scores = self._coerce_scores(
            {"scores": selection.get("scores") or {symbol: 1.0 for symbol in selected_symbols}},
            universe_set,
        )
        timing = value.get("timing")
        if not isinstance(timing, dict):
            raise ValueError("compute_exposure must return {'exposure': number}")
        try:
            exposure = float(timing.get("exposure"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Timing exposure must be numeric") from exc
        if not np.isfinite(exposure) or not 0 <= exposure <= 1:
            raise ValueError("Timing exposure must be finite and in [0, 1]")
        weights = self._coerce_weights(
            {"weights": value.get("weights")}, universe_set, label="risk"
        )
        weights = self._validate_target_weights(config, weights, universe_set)
        coverage = (
            factor_scores.notna().mean(axis=1)
            if not factor_scores.empty
            else pd.Series(1.0, index=universe_symbols, dtype=float)
        )
        ordered_scores = dict(sorted(scores.items(), key=lambda item: item[1], reverse=True))
        runtime = {
            "entrypoint": "run_strategy",
            "source_sha256": execution.source_sha256,
            "duration_seconds": execution.duration_seconds,
            "stdout": execution.stdout,
            "stderr": execution.stderr,
        }
        self._finish_selection_snapshot(
            config,
            ordered_scores,
            factor_scores,
            coverage,
            weights,
            {"complete": runtime},
        )
        self._diagnostics.update(
            {
                "implementation": "python",
                "pipeline_kind": "six_stage",
                "universe_count": len(universe_symbols),
                "timing_exposure": exposure,
                "complete_pipeline": value,
                "python": {"complete": runtime},
            }
        )
        return weights

    def _market_returns_context(self, as_of: pd.Timestamp) -> list[dict]:
        start = (as_of - pd.DateOffset(months=48)).strftime("%Y-%m-%d")
        end = as_of.strftime("%Y-%m-%d")
        try:
            frame = self._data.get_factors(
                ["MKT"], start, end, freq="1M", strict=False, use_cache=False
            )
        except MissingDataError:
            return []
        if frame.empty or "MKT" not in frame:
            return []
        return [
            {"date": pd.Timestamp(date).strftime("%Y-%m-%d"), "value": float(value)}
            for date, value in pd.to_numeric(frame["MKT"], errors="coerce").dropna().items()
            if pd.Timestamp(date) <= as_of
        ]

    def _candidate_context(
        self,
        config: StrategyConfig,
        as_of: pd.Timestamp,
        symbols: list[str],
        data_by_symbol: dict[str, pd.DataFrame],
        scores: pd.DataFrame,
        current_weights: dict[str, float],
        lookback_days: int,
    ) -> dict:
        candidates = []
        history_limit = min(max(lookback_days, 20), 240)
        for symbol in symbols:
            history = data_by_symbol[symbol].tail(history_limit).copy()
            fields = [
                field
                for field in ("open", "high", "low", "close", "volume", "amount")
                if field in history
            ]
            compact = history[["date", *fields]].copy()
            compact["date"] = pd.to_datetime(compact["date"]).dt.strftime("%Y-%m-%d")
            for field in fields:
                compact[field] = pd.to_numeric(compact[field], errors="coerce")
            compact = compact.astype(object).where(pd.notna(compact), None)
            rows = compact.to_dict("records")
            factor_values = {}
            if symbol in scores.index:
                factor_values = {
                    str(name): float(value)
                    for name, value in scores.loc[symbol].dropna().items()
                }
            candidates.append(
                {
                    "symbol": symbol,
                    "history": rows,
                    "factor_scores": factor_values,
                }
            )
        return {
            "strategy_type": "stock_selection",
            "strategy_id": config.name,
            "as_of_date": as_of.strftime("%Y-%m-%d"),
            "candidates": candidates,
            "current_weights": dict(current_weights),
            "limits": {
                "max_weight": config.portfolio.max_weight,
                "max_stocks": config.selection.n_stocks,
            },
            "factor_names": list(config.factor_names),
            "settings": {
                "min_factor_coverage": config.selection.min_factor_coverage,
            },
            "metadata": config.metadata,
        }

    @staticmethod
    def _run_python_stage(stage, python_source: str | None, context: dict) -> tuple[object, dict]:
        if python_source is None:
            raise ValueError("python_source is required when a pipeline stage uses Python")
        execution = execute_python_strategy(
            python_source,
            stage.entrypoint,
            [context],
            stage.timeout_seconds,
        )
        return execution.values[0], {
            "entrypoint": stage.entrypoint,
            "source_sha256": execution.source_sha256,
            "duration_seconds": execution.duration_seconds,
            "stdout": execution.stdout,
            "stderr": execution.stderr,
        }

    @staticmethod
    def _coerce_scores(value: object, allowed: set[str]) -> dict[str, float]:
        if not isinstance(value, dict) or not isinstance(value.get("scores"), dict):
            raise ValueError("Python signal stage must return {'scores': {...}}")
        scores: dict[str, float] = {}
        for raw_symbol, raw_score in value["scores"].items():
            symbol = str(raw_symbol).strip().upper()
            if symbol not in allowed:
                raise ValueError(f"Python signal stage returned an ineligible symbol: {symbol}")
            try:
                score = float(raw_score)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Python signal score for {symbol} must be numeric") from exc
            if not np.isfinite(score):
                raise ValueError(f"Python signal score for {symbol} must be finite")
            scores[symbol] = score
        return scores

    @staticmethod
    def _coerce_weights(
        value: object,
        allowed: set[str],
        *,
        label: str,
    ) -> dict[str, float]:
        if not isinstance(value, dict) or not isinstance(value.get("weights"), dict):
            raise ValueError(f"Python {label} stage must return {{'weights': {{...}}}}")
        weights: dict[str, float] = {}
        for raw_symbol, raw_weight in value["weights"].items():
            symbol = str(raw_symbol).strip().upper()
            if symbol not in allowed:
                raise ValueError(f"Python {label} stage returned an ineligible symbol: {symbol}")
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Python {label} weight for {symbol} must be numeric") from exc
            if not np.isfinite(weight) or weight < 0:
                raise ValueError(f"Python {label} weight for {symbol} must be finite and non-negative")
            if weight > 1e-12:
                weights[symbol] = weight
        return weights

    @staticmethod
    def _validate_target_weights(
        config: StrategyConfig,
        weights: dict[str, float],
        allowed: set[str],
    ) -> dict[str, float]:
        unknown = sorted(set(weights) - allowed)
        if unknown:
            raise ValueError(f"Risk stage returned ineligible symbols: {unknown}")
        if len(weights) > config.selection.n_stocks:
            raise ValueError(
                f"Risk stage returned {len(weights)} stocks; limit is {config.selection.n_stocks}"
            )
        for symbol, weight in weights.items():
            if not np.isfinite(weight) or weight < 0:
                raise ValueError(f"Risk-stage weight for {symbol} must be finite and non-negative")
            if weight > config.portfolio.max_weight + 1e-9:
                raise ValueError(
                    f"Risk-stage weight for {symbol} exceeds max_weight "
                    f"{config.portfolio.max_weight:g}"
                )
        total = float(sum(weights.values()))
        if total > 1.0 + 1e-9:
            raise ValueError("Risk-stage weights must sum to at most 1.0")
        return {symbol: weight for symbol, weight in weights.items() if weight > 1e-12}

    def _finish_selection_snapshot(
        self,
        config: StrategyConfig,
        ordered_scores: dict[str, float],
        factor_scores: pd.DataFrame,
        coverage: pd.Series,
        weights: dict[str, float],
        python_runtime: dict[str, dict],
    ) -> None:
        preview_limit = min(max(config.selection.n_stocks * 2, 20), 100)
        preview_rows = []
        for rank, (symbol, score) in enumerate(
            list(ordered_scores.items())[:preview_limit], start=1
        ):
            factor_values = (
                {
                    str(name): float(factor_score)
                    for name, factor_score in factor_scores.loc[symbol].dropna().items()
                }
                if symbol in factor_scores.index
                else {}
            )
            preview_rows.append(
                {
                    "rank": rank,
                    "symbol": symbol,
                    "selected": symbol in weights,
                    "composite_score": score,
                    "factor_coverage": (
                        float(coverage.get(symbol, 1.0))
                    ),
                    "target_weight": float(weights.get(symbol, 0.0)),
                    "factor_scores": factor_values,
                }
            )
        total = float(sum(weights.values()))
        self._diagnostics.update(
            {
                "implementation": config.pipeline.implementation_summary,
                "python_stages": list(config.pipeline.python_stages),
                "score_count": len(ordered_scores),
                "selected_count": len(weights),
                "selected_symbols": list(weights),
                "weight_sum": total,
                "cash_weight": max(0.0, 1.0 - total),
                "python": python_runtime,
            }
        )
        self._selection_snapshot.update(
            {
                "scored_count": len(ordered_scores),
                "selected_count": len(weights),
                "cash_weight": max(0.0, 1.0 - total),
                "rows": preview_rows,
                "python": python_runtime,
            }
        )

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
    python_source: str | None = None,
    complete_python_source: str | None = None,
    stage_parameters: dict[str, dict] | None = None,
) -> tuple[pd.Series, pd.DataFrame]:
    """Run the configured backtest and return its net returns and holdings."""

    result = run_backtest_detailed(
        config,
        start_date,
        end_date,
        data_engine=data_engine,
        lookback_days=lookback_days,
        python_source=python_source,
        complete_python_source=complete_python_source,
        stage_parameters=stage_parameters,
    )
    return result.returns, result.weights


def run_backtest_detailed(
    config: ConfigOrPath,
    start_date: str,
    end_date: str,
    data_engine: DataEngine | None = None,
    lookback_days: int = 120,
    python_source: str | None = None,
    complete_python_source: str | None = None,
    stage_parameters: dict[str, dict] | None = None,
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
        fields=["open", "high", "low", "close", "volume", "amount"],
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
            python_source=python_source,
            current_weights=previous,
            complete_python_source=complete_python_source,
            stage_parameters=stage_parameters,
            bars_override=bars,
        )
        custom_decision = complete_python_source is not None or any(
            getattr(cfg.pipeline, name).kind == "python"
            for name in ("signal", "portfolio", "risk")
        )
        if not target and not custom_decision:
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
        if complete_python_source is not None:
            period_cfg, execution_runtime = _complete_execution_config_for_period(
                cfg, signal_diagnostics.get("complete_pipeline")
            )
        else:
            period_cfg, execution_runtime = _execution_config_for_period(
                cfg,
                python_source,
                target,
                previous,
                signal_date,
                entry_date,
            )
        executed, execution = _apply_execution_constraints(
            target,
            previous,
            bars,
            entry_date,
            period_cfg,
        )
        asset_returns = _asset_period_returns(
            bars,
            executed,
            entry_date,
            exit_date,
            period_cfg.execution.execution_price,
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
                "execution_price": period_cfg.execution.execution_price,
                "execution_settings": asdict(period_cfg.execution),
                "pipeline_execution": execution_runtime,
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
            "pipeline": cfg.pipeline.to_dict(),
            "pipeline_kind": "six_stage" if complete_python_source is not None else "internal",
            "complete_python_source_sha256": (
                python_source_sha256(complete_python_source)
                if complete_python_source is not None
                else None
            ),
            "periods": len(returns_series),
            "warnings": sorted(warnings),
        },
    )


def _complete_execution_config_for_period(
    config: StrategyConfig,
    pipeline_result: object,
) -> tuple[StrategyConfig, dict | None]:
    if not isinstance(pipeline_result, dict):
        raise ValueError("complete strategy result is missing")
    stage = pipeline_result.get("execution")
    if not isinstance(stage, dict) or not isinstance(stage.get("execution"), dict):
        raise ValueError("create_orders must return {'execution': {...}}")
    overrides = dict(stage["execution"])
    overrides.pop("rebalance_freq", None)
    allowed = set(ExecutionSpec.__dataclass_fields__)
    unknown = sorted(set(overrides) - allowed)
    if unknown:
        raise ValueError(f"Execution stage returned unsupported fields: {unknown}")
    return replace(config, execution=ExecutionSpec(**{**asdict(config.execution), **overrides})), {
        "entrypoint": "run_strategy",
        "complete": True,
    }


def _execution_config_for_period(
    config: StrategyConfig,
    python_source: str | None,
    target: dict[str, float],
    current: dict[str, float],
    signal_date: pd.Timestamp,
    entry_date: pd.Timestamp,
) -> tuple[StrategyConfig, dict | None]:
    stage = config.pipeline.execution
    context = {
        "strategy_type": "stock_selection",
        "strategy_id": config.name,
        "signal_date": signal_date.strftime("%Y-%m-%d"),
        "entry_date": entry_date.strftime("%Y-%m-%d"),
        "target_weights": dict(target),
        "current_weights": dict(current),
        "configured_execution": asdict(config.execution),
        "metadata": config.metadata,
    }
    if stage.kind == "configured":
        value = configured_stock_execution(context)
        runtime = None
    else:
        value, runtime = SignalEngine._run_python_stage(stage, python_source, context)
    if not isinstance(value, dict) or not isinstance(value.get("execution"), dict):
        raise ValueError("Python execution stage must return {'execution': {...}}")
    overrides = dict(value["execution"])
    allowed = set(ExecutionSpec.__dataclass_fields__)
    unknown = sorted(set(overrides) - allowed)
    if unknown:
        raise ValueError(f"Python execution stage returned unsupported fields: {unknown}")
    execution = ExecutionSpec(**{**asdict(config.execution), **overrides})
    return replace(config, execution=execution), runtime


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
