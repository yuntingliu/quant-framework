"""One event-driven research and backtest engine for Strategy SDK v1."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from alphalab.analytics.factor_evidence import factor_research_report
from alphalab.dataio import DataEngine, MissingDataError
from alphalab.strategy.config import ExecutionSpec
from alphalab.strategy.repository import StrategyRepository
from alphalab.strategy.sdk_runtime import SdkExecutionSession

_EXECUTION_COUNTER_FIELDS = (
    "attempted_trade_count",
    "successful_trade_count",
    "execution_data_fill_count",
    "synthetic_state_count",
    "market_state_rejection_count",
    "suspension_rejection_count",
    "limit_up_rejection_count",
    "limit_down_rejection_count",
    "capacity_rejection_count",
    "cash_rejection_count",
)


def _empty_execution_summary() -> dict[str, int]:
    return {field: 0 for field in _EXECUTION_COUNTER_FIELDS}


@dataclass(frozen=True)
class StrategyBacktestResult:
    project: dict[str, Any]
    package: dict[str, Any]
    returns: pd.Series
    weights: pd.DataFrame
    benchmark_symbols: tuple[str, ...]
    executions: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class PreparedRunData:
    sessions: tuple[pd.Timestamp, ...]
    bars: pd.DataFrame
    instruments: pd.DataFrame
    fundamentals: pd.DataFrame
    daily_factors: pd.DataFrame
    index_components: pd.DataFrame
    all_symbols: tuple[str, ...]


def preview_strategy(
    repository: StrategyRepository,
    project_id: str,
    data_engine: DataEngine,
    as_of_date: str,
    *,
    operation: str = "execution",
    revision: int | None = None,
) -> dict[str, Any]:
    if operation not in {"signal", "portfolio", "execution"}:
        raise ValueError("operation must be signal, portfolio, or execution")
    project, package = _project_package(repository, project_id, revision)
    as_of = pd.Timestamp(as_of_date)
    lookback = int(project["settings"].get("lookback_days", 260))
    prepared = _prepare_data(
        data_engine,
        package,
        as_of - pd.Timedelta(days=max(lookback * 2, 365)),
        as_of,
    )
    available = _available_symbols(prepared.instruments, as_of, _rows_on(prepared.bars, as_of))
    payload = _static_payload(prepared)
    with SdkExecutionSession(package["source"], timeout_seconds=20.0) as session:
        session.configure(payload)
        result = session.execute(
            operation,
            _event_request(
                project,
                as_of,
                available,
                event="session_close",
                portfolio={},
                state={},
                last_decision=None,
                force_signal=True,
            ),
        )
    return {
        "project_id": project_id,
        "revision": package["revision"],
        "source_sha256": package["source_sha256"],
        "profile": project["profile"],
        "operation": operation,
        "result": result.value,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def evaluate_factor_snapshot(
    repository: StrategyRepository,
    project_id: str,
    factor_id: str,
    data_engine: DataEngine,
    as_of_date: str,
    *,
    revision: int | None = None,
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    project, package = _project_package(repository, project_id, revision)
    as_of = pd.Timestamp(as_of_date)
    lookback = int(project["settings"].get("lookback_days", 260))
    prepared = _prepare_data(
        data_engine,
        package,
        as_of - pd.Timedelta(days=max(lookback * 2, 365)),
        as_of,
    )
    available = _available_symbols(prepared.instruments, as_of, _rows_on(prepared.bars, as_of))
    with SdkExecutionSession(package["source"], timeout_seconds=20.0) as session:
        session.configure(_static_payload(prepared))
        result = session.execute(
            "factor",
            {
                "event": "session_close",
                "as_of": as_of,
                "available_symbols": available,
                "factor_id": factor_id,
                "parameters": dict(parameters or {}),
                "portfolio": {},
                "state": {},
                "limits": _limits(project),
            },
        )
    return {
        "project_id": project_id,
        "revision": package["revision"],
        "source_sha256": package["source_sha256"],
        "profile": project["profile"],
        **result.value,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def evaluate_factor_history(
    repository: StrategyRepository,
    project_id: str,
    factor_id: str,
    data_engine: DataEngine,
    start_date: str,
    end_date: str,
    *,
    revision: int | None = None,
    parameters: Mapping[str, Any] | None = None,
    frequency: str = "monthly",
) -> dict[str, Any]:
    if frequency not in {"daily", "weekly", "monthly"}:
        raise ValueError("frequency must be daily, weekly, or monthly")
    project, package = _project_package(repository, project_id, revision)
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
    if start >= end:
        raise ValueError("start_date must be before end_date")
    lookback = int(project["settings"].get("lookback_days", 260))
    prepared = _prepare_data(
        data_engine,
        package,
        start - pd.Timedelta(days=max(lookback * 2, 365)),
        end,
    )
    evaluation_dates = _evaluation_dates(prepared.sessions, start, end, frequency)
    snapshots: list[dict[str, Any]] = []
    invoked: set[str] = set()
    with SdkExecutionSession(package["source"], timeout_seconds=20.0) as session:
        session.configure(_static_payload(prepared))
        for as_of in evaluation_dates:
            result = session.execute(
                "factor",
                {
                    "event": "session_close",
                    "as_of": as_of,
                    "available_symbols": _available_symbols(
                        prepared.instruments, as_of, _rows_on(prepared.bars, as_of)
                    ),
                    "factor_id": factor_id,
                    "parameters": dict(parameters or {}),
                    "portfolio": {},
                    "state": {},
                    "limits": _limits(project),
                },
            )
            invoked.update(result.value.get("invoked") or ())
            snapshots.append(
                {
                    "date": str(as_of)[:10],
                    "values": result.value["values"],
                }
            )
    return {
        "project_id": project_id,
        "revision": package["revision"],
        "source_sha256": package["source_sha256"],
        "profile": project["profile"],
        "factor_id": factor_id,
        "frequency": frequency,
        "snapshots": snapshots,
        "observations": len(snapshots),
        "invoked": sorted(invoked),
    }


def evaluate_factor_research(
    repository: StrategyRepository,
    project_id: str,
    factor_id: str,
    data_engine: DataEngine,
    start_date: str,
    end_date: str,
    *,
    revision: int | None = None,
    parameters: Mapping[str, Any] | None = None,
    frequency: str = "monthly",
    quantiles: int = 5,
    horizons: Sequence[int] = (1, 3, 6),
) -> dict[str, Any]:
    """Evaluate one saved factor with later open-to-open evidence."""

    if frequency not in {"daily", "weekly", "monthly"}:
        raise ValueError("frequency must be daily, weekly, or monthly")
    project, package = _project_package(repository, project_id, revision)
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
    if start >= end:
        raise ValueError("start_date must be before end_date")
    lookback = int(project["settings"].get("lookback_days", 260))
    prepared = _prepare_data(
        data_engine,
        package,
        start - pd.Timedelta(days=max(lookback * 2, 365)),
        end,
    )
    evaluation_dates = _evaluation_dates(prepared.sessions, start, end, frequency)
    snapshots: list[dict[str, Any]] = []
    invoked: set[str] = set()
    with SdkExecutionSession(package["source"], timeout_seconds=20.0) as session:
        session.configure(_static_payload(prepared))
        for as_of in evaluation_dates:
            result = session.execute(
                "factor",
                {
                    "event": "session_close",
                    "as_of": as_of,
                    "available_symbols": _available_symbols(
                        prepared.instruments, as_of, _rows_on(prepared.bars, as_of)
                    ),
                    "factor_id": factor_id,
                    "parameters": dict(parameters or {}),
                    "portfolio": {},
                    "state": {},
                    "limits": _limits(project),
                },
            )
            invoked.update(result.value.get("invoked") or ())
            snapshots.append(
                {
                    "date": str(as_of)[:10],
                    "values": result.value["values"],
                    "input_audit": _factor_input_audit(prepared, as_of),
                }
            )
    report = factor_research_report(
        snapshots,
        prepared.bars,
        frequency=frequency,
        quantiles=quantiles,
        horizons=horizons,
    )
    return {
        "project_id": project_id,
        "revision": package["revision"],
        "source_sha256": package["source_sha256"],
        "profile": project["profile"],
        "factor_id": factor_id,
        "invoked": sorted(invoked),
        **report,
    }


def run_strategy_backtest(
    repository: StrategyRepository,
    project_id: str,
    start_date: str,
    end_date: str,
    data_engine: DataEngine,
    *,
    revision: int | None = None,
    seed: int = 0,
    execution_data_policy: str = "strict",
) -> StrategyBacktestResult:
    if execution_data_policy not in {"strict", "illustrative"}:
        raise ValueError("execution_data_policy must be strict or illustrative")
    project, package = _project_package(repository, project_id, revision)
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
    if start >= end:
        raise ValueError("start_date must be before end_date")
    lookback = int(project["settings"].get("lookback_days", 260))
    prepared = _prepare_data(
        data_engine,
        package,
        start - pd.Timedelta(days=max(lookback * 2, 365)),
        end,
        execution_data_policy=execution_data_policy,
    )
    sessions = tuple(value for value in prepared.sessions if start <= value <= end)
    if len(sessions) < 2:
        return StrategyBacktestResult(
            project=project,
            package=package,
            returns=pd.Series(dtype=float, name=project_id),
            weights=pd.DataFrame(),
            benchmark_symbols=prepared.all_symbols,
            executions=(),
            diagnostics={
                "sdk_version": 1,
                "source_sha256": package["source_sha256"],
                "revision": package["revision"],
                "warnings": ["insufficient sessions"],
                "execution_data_policy": execution_data_policy,
                "research_valid": False,
                "execution_data_exclusions": {
                    "symbol_date_count": 0,
                    "unique_symbol_count": 0,
                    "symbols_sample": [],
                    "samples": [],
                },
                "execution_data_fill": {
                    "value_count": 0,
                    "fields": {},
                    "source_counts": {
                        "provider": {},
                        "strategy_fill": {},
                        "fallback": {},
                    },
                },
                "execution_summary": _empty_execution_summary(),
                "execution_fidelity": {
                    "mean": None,
                    "minimum": None,
                    "low_fidelity_period_count": 0,
                    "persistent_tracking_error_periods": 0,
                    "maximum_target_weight_deviation": 0.0,
                    "persistent_exit_failure_symbols": [],
                },
                "research_invalid_reasons": ["INSUFFICIENT_SESSIONS"],
                "signal_evidence": {"rows": [], "periods": 0, "evidence_periods": 0},
            },
        )

    bars_by_date = {
        pd.Timestamp(date): frame.drop_duplicates("symbol", keep="last").set_index("symbol")
        for date, frame in prepared.bars.groupby("date")
    }
    state: dict[str, Any] = {}
    last_decision: dict[str, Any] | None = None
    pending_open: dict[str, Any] | None = None
    pending_close: dict[str, Any] | None = None
    asset_values: dict[str, float] = {}
    entry_prices: dict[str, float] = {}
    cash_value = 1.0
    previous_close_prices: dict[str, float] = {}
    previous_nav = 1.0
    returns: dict[pd.Timestamp, float] = {}
    weights: dict[pd.Timestamp, dict[str, float]] = {}
    executions: list[dict[str, Any]] = []
    event_diagnostics: list[dict[str, Any]] = []
    signal_evidence_rows: list[dict[str, Any]] = []
    pending_signal_evidence: dict[str, Any] | None = None
    delisting_settlements: list[dict[str, Any]] = []
    warnings: set[str] = set()
    execution_exclusion_count = 0
    execution_exclusion_symbols: set[str] = set()
    execution_exclusion_samples: list[dict[str, Any]] = []
    execution_fill_count = 0
    execution_fill_fields: dict[str, int] = {}
    if execution_data_policy == "illustrative":
        warnings.add(
            "illustrative execution data: suspension and price-limit coverage is not guaranteed"
        )
    run_manifest = list(package.get("manifest") or [])
    has_execution_data_fill = any(
        item.get("kind") == "execution_data_fill" for item in run_manifest
    )

    with SdkExecutionSession(package["source"], timeout_seconds=30.0) as session:
        session.configure(_static_payload(prepared))
        for session_index, current_date in enumerate(sessions):
            rows = bars_by_date.get(current_date, pd.DataFrame())
            available = _available_symbols(prepared.instruments, current_date, rows)

            delisted = _settle_delisted_positions(
                asset_values,
                entry_prices,
                previous_close_prices,
                prepared.instruments,
                current_date,
            )
            if delisted:
                delisting_settlements.append(
                    {
                        "date": str(current_date)[:10],
                        "symbols": [item["symbol"] for item in delisted],
                        "position_count": len(delisted),
                        "written_off_value": float(
                            sum(float(item["written_off_value"]) for item in delisted)
                        ),
                    }
                )

            open_prices = _prices(rows, "open")
            close_prices = _prices(rows, "close")
            # Carry the actual previous-close book through the overnight gap.
            for symbol in list(asset_values):
                old_close = previous_close_prices.get(symbol)
                new_open = open_prices.get(symbol)
                if old_close and new_open and old_close > 0:
                    asset_values[symbol] *= new_open / old_close

            open_nav = cash_value + sum(asset_values.values())
            if not math.isfinite(open_nav) or open_nav <= 0:
                raise ValueError("portfolio NAV became invalid before session open")

            if pending_open is not None:
                executing_open = pending_open
                pending_open = None
                asset_values, cash_value, entry_prices, audit = _execute_target(
                    executing_open,
                    asset_values,
                    cash_value,
                    entry_prices,
                    rows,
                    open_nav,
                    field="open",
                    project=project,
                    strict_execution_data=execution_data_policy == "strict",
                    session=session,
                    as_of=current_date,
                    event="session_open",
                    execution_data_fill=(
                        has_execution_data_fill and execution_data_policy == "strict"
                    ),
                )
                execution_fill_count, execution_fill_fields = _record_execution_data_audit(
                    audit,
                    current_date,
                    execution_exclusion_symbols,
                    execution_exclusion_samples,
                    execution_fill_count,
                    execution_fill_fields,
                )
                execution_exclusion_count += len(audit.get("missing_execution_data") or ())
                executions.append(
                    {
                        **audit,
                        "decision_date": executing_open["decision_date"],
                        "entry_date": str(current_date)[:10],
                        "activation": "next_session_open",
                    }
                )
                state, last_decision, pending_open, pending_close = _dispatch_execution_events(
                    session,
                    project,
                    current_date,
                    available,
                    asset_values,
                    cash_value,
                    entry_prices,
                    open_prices,
                    state,
                    last_decision,
                    seed + session_index,
                    audit,
                    run_manifest,
                    prepared.sessions,
                    pending_open,
                    pending_close,
                )

            if _event_needed(run_manifest, "session_open", current_date, prepared.sessions):
                state, last_decision, open_decision, event_value = _run_event(
                    session,
                    project,
                    current_date,
                    available,
                    "session_open",
                    asset_values,
                    cash_value,
                    entry_prices,
                    open_prices,
                    state,
                    last_decision,
                    seed + session_index,
                    include_value=True,
                )
                event_diagnostics.append(
                    _compact_event_diagnostic(current_date, "session_open", event_value)
                )
                pending_signal_evidence = _record_signal_evidence(
                    event_value,
                    current_date,
                    open_prices,
                    signal_evidence_rows,
                    pending_signal_evidence,
                )
                if open_decision:
                    pending_open, pending_close = _queue_decision(
                        open_decision, current_date, pending_open, pending_close
                    )

            # Mark the actual open book to the close before close-event code runs.
            for symbol in list(asset_values):
                start_price = open_prices.get(symbol)
                finish_price = close_prices.get(symbol)
                if start_price and finish_price and start_price > 0:
                    asset_values[symbol] *= finish_price / start_price

            close_nav = cash_value + sum(asset_values.values())
            if pending_close is not None:
                executing_close = pending_close
                pending_close = None
                asset_values, cash_value, entry_prices, audit = _execute_target(
                    executing_close,
                    asset_values,
                    cash_value,
                    entry_prices,
                    rows,
                    close_nav,
                    field="close",
                    project=project,
                    strict_execution_data=execution_data_policy == "strict",
                    session=session,
                    as_of=current_date,
                    event="session_close",
                    execution_data_fill=(
                        has_execution_data_fill and execution_data_policy == "strict"
                    ),
                )
                execution_fill_count, execution_fill_fields = _record_execution_data_audit(
                    audit,
                    current_date,
                    execution_exclusion_symbols,
                    execution_exclusion_samples,
                    execution_fill_count,
                    execution_fill_fields,
                )
                execution_exclusion_count += len(audit.get("missing_execution_data") or ())
                executions.append(
                    {
                        **audit,
                        "decision_date": executing_close["decision_date"],
                        "entry_date": str(current_date)[:10],
                        "activation": "next_session_close",
                    }
                )
                close_nav = cash_value + sum(asset_values.values())
                state, last_decision, pending_open, pending_close = _dispatch_execution_events(
                    session,
                    project,
                    current_date,
                    available,
                    asset_values,
                    cash_value,
                    entry_prices,
                    close_prices,
                    state,
                    last_decision,
                    seed + session_index,
                    audit,
                    run_manifest,
                    prepared.sessions,
                    pending_open,
                    pending_close,
                )

            if _event_needed(run_manifest, "session_close", current_date, prepared.sessions):
                state, last_decision, close_decision, event_value = _run_event(
                    session,
                    project,
                    current_date,
                    available,
                    "session_close",
                    asset_values,
                    cash_value,
                    entry_prices,
                    close_prices,
                    state,
                    last_decision,
                    seed + session_index,
                    include_value=True,
                )
                event_diagnostics.append(
                    _compact_event_diagnostic(current_date, "session_close", event_value)
                )
                pending_signal_evidence = _record_signal_evidence(
                    event_value,
                    current_date,
                    close_prices,
                    signal_evidence_rows,
                    pending_signal_evidence,
                )
                if close_decision:
                    pending_open, pending_close = _queue_decision(
                        close_decision, current_date, pending_open, pending_close
                    )

            close_nav = cash_value + sum(asset_values.values())
            daily_return = close_nav / previous_nav - 1.0
            if not math.isfinite(daily_return):
                raise ValueError("daily strategy return became non-finite")
            returns[current_date] = float(daily_return)
            weights[current_date] = {
                symbol: float(value / close_nav)
                for symbol, value in asset_values.items()
                if value > 1e-12
            }
            previous_nav = close_nav
            previous_close_prices = close_prices

    returns_series = pd.Series(returns, name=project_id, dtype=float)
    weights_frame = (
        pd.DataFrame.from_dict(weights, orient="index").reindex(returns_series.index).fillna(0.0)
    )
    if execution_exclusion_count:
        warnings.add(
            "PARTIAL_MARKET_STATE: strict execution excluded "
            f"{execution_exclusion_count} symbol-date candidates with missing "
            "suspension or price-limit data"
        )
    if execution_fill_count:
        warnings.add(
            "CUSTOM_EXECUTION_DATA_FILL: project Python filled "
            f"{execution_fill_count} missing execution-state values"
        )
    execution_summary = _aggregate_execution_summary(executions)
    execution_state_source_counts = _aggregate_execution_state_sources(executions)
    execution_fidelity = _execution_fidelity_summary(executions)
    research_invalid_reasons: list[str] = []
    if execution_data_policy != "strict":
        research_invalid_reasons.append("ILLUSTRATIVE_EXECUTION_DATA")
    if execution_summary["market_state_rejection_count"]:
        research_invalid_reasons.append("MISSING_EXECUTION_STATE")
    if execution_fidelity["persistent_exit_failure_symbols"]:
        research_invalid_reasons.append("PERSISTENT_EXIT_FAILURE")
    if (
        (
            execution_summary["attempted_trade_count"] > 0
            and execution_summary["successful_trade_count"] == 0
        )
        or execution_fidelity["persistent_tracking_error_periods"] >= 2
        or (
            execution_fidelity["low_fidelity_period_count"] >= 2
            and execution_fidelity["mean"] is not None
            and execution_fidelity["mean"] < 0.8
        )
    ):
        research_invalid_reasons.append("LOW_EXECUTION_FIDELITY")
    if "PERSISTENT_EXIT_FAILURE" in research_invalid_reasons:
        warnings.add(
            "PERSISTENT_EXIT_FAILURE: one or more positions repeatedly failed to reach "
            "their requested lower or zero target"
        )
    if "LOW_EXECUTION_FIDELITY" in research_invalid_reasons:
        warnings.add(
            "LOW_EXECUTION_FIDELITY: the actual portfolio repeatedly remained materially "
            "different from the requested target"
        )
    return StrategyBacktestResult(
        project=project,
        package=package,
        returns=returns_series,
        weights=weights_frame,
        benchmark_symbols=prepared.all_symbols,
        executions=tuple(executions),
        diagnostics={
            "sdk_version": 1,
            "project_id": project_id,
            "revision": package["revision"],
            "source_sha256": package["source_sha256"],
            "strategy_source_package": {
                "project_id": project_id,
                "revision": package["revision"],
                "source_sha256": package["source_sha256"],
                "validator_version": package["validator_version"],
                "environment_sha256": package["environment"].get("sha256"),
            },
            "periods": len(returns_series),
            "events": event_diagnostics,
            "delisting_settlements": delisting_settlements,
            "final_state": state,
            "warnings": sorted(warnings),
            "execution_data_policy": execution_data_policy,
            "research_valid": not research_invalid_reasons,
            "research_invalid_reasons": research_invalid_reasons,
            "execution_data_exclusions": {
                "symbol_date_count": execution_exclusion_count,
                "unique_symbol_count": len(execution_exclusion_symbols),
                "symbols_sample": sorted(execution_exclusion_symbols)[:50],
                "samples": execution_exclusion_samples,
            },
            "execution_data_fill": {
                "value_count": execution_fill_count,
                "fields": dict(sorted(execution_fill_fields.items())),
                "source_counts": execution_state_source_counts,
            },
            "execution_summary": execution_summary,
            "execution_fidelity": execution_fidelity,
            "signal_evidence": {
                "rows": signal_evidence_rows,
                "periods": len(signal_evidence_rows),
                "evidence_periods": sum(
                    1 for item in signal_evidence_rows if item.get("ic") is not None
                ),
            },
        },
    )


def _project_package(
    repository: StrategyRepository,
    project_id: str,
    revision: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = repository.get_project(project_id, include_source=False)
    if project is None:
        raise KeyError(project_id)
    package = repository.get_package(project_id, revision)
    if package is None:
        raise KeyError(f"{project_id}@{revision}")
    return project, package


def _prepare_data(
    engine: DataEngine,
    package: Mapping[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    execution_data_policy: str = "none",
) -> PreparedRunData:
    if execution_data_policy not in {"none", "strict", "illustrative"}:
        raise ValueError("execution_data_policy must be none, strict, or illustrative")
    # RQ ``all_instruments`` is an instrument master stamped with its retrieval
    # date.  Its listing intervals are still valid for earlier research dates.
    # Loading it through an end-date view drops instruments that delisted during
    # the requested period, while treating the retrieval stamp as an effective
    # date leaves every earlier session with an empty universe.  Keep the master
    # rows that overlap this prepared range, then make that master available from
    # the beginning of the range; StrategyContext and _available_symbols apply
    # the listing intervals at each point in time.
    master_loader = getattr(engine, "get_instrument_master", None)
    instruments = master_loader() if callable(master_loader) else engine.get_instruments(None)
    if instruments.empty or "symbol" not in instruments:
        raise MissingDataError("point-in-time instrument snapshots are required")
    instruments = instruments.copy()
    instruments["symbol"] = instruments["symbol"].astype(str).str.upper()
    listed_field = next(
        (name for name in ("listed_date", "list_date") if name in instruments), None
    )
    delisted_field = next(
        (name for name in ("de_listed_date", "delisted_date") if name in instruments),
        None,
    )
    if listed_field is not None:
        listed = pd.to_datetime(instruments[listed_field], errors="coerce")
        instruments = instruments.loc[listed.isna() | listed.le(end)].copy()
    if delisted_field is not None:
        delisted = pd.to_datetime(instruments[delisted_field], errors="coerce")
        instruments = instruments.loc[delisted.isna() | delisted.gt(start)].copy()
    if instruments.empty:
        raise MissingDataError("no instruments overlap the selected research range")
    if "snapshot_date" in instruments:
        instruments["source_snapshot_date"] = instruments["snapshot_date"]
    instruments["snapshot_date"] = pd.Timestamp(start).normalize()
    instruments = instruments.drop_duplicates("symbol", keep="last").reset_index(drop=True)
    symbols = tuple(sorted(instruments["symbol"].dropna().unique()))
    declared_bar_fields = list(
        dict.fromkeys(
            package.get("data_requirements", {}).get("bars")
            or ["open", "high", "low", "close", "volume", "amount"]
        )
    )
    required_execution_fields = ["open", "close", "volume", "amount"]
    has_execution_data_fill = any(
        item.get("kind") == "execution_data_fill" for item in package.get("manifest") or ()
    )
    supplemental_execution_fields = (
        [
            "limit_up",
            "limit_down",
            "raw_open",
            "raw_close",
            *(["is_st"] if has_execution_data_fill else []),
        ]
        if execution_data_policy == "strict"
        else []
    )
    required_bar_fields = list(dict.fromkeys([*declared_bar_fields, *required_execution_fields]))
    fields = list(dict.fromkeys([*required_bar_fields, *supplemental_execution_fields]))
    state_field_names = {"paused", "is_suspended", "is_st"}
    requested_state_fields = list(
        dict.fromkeys(
            [
                *(field for field in fields if field in state_field_names),
                *(["is_suspended"] if execution_data_policy != "none" else []),
            ]
        )
    )
    market_fields = [field for field in fields if field not in state_field_names]
    bars = engine.get_bars(
        list(symbols),
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
        fields=market_fields,
        strict=False,
        use_cache=False,
    )
    if bars.empty:
        raise MissingDataError("no market bars for the selected StrategySourcePackage")
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"])
    market_state_loader = getattr(engine, "get_market_state", None)
    market_state = (
        market_state_loader(
            list(symbols),
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
            fields=requested_state_fields,
            use_cache=False,
        )
        if callable(market_state_loader)
        else pd.DataFrame()
    )
    if not market_state.empty:
        market_state = market_state.copy()
        market_state["date"] = pd.to_datetime(market_state["date"], errors="coerce")
        bars = bars.merge(
            market_state.drop_duplicates(["date", "symbol"], keep="last"),
            on=["date", "symbol"],
            how="left",
        )
    missing = sorted(set(required_bar_fields) - set(bars.columns))
    if missing:
        raise MissingDataError(f"data profile is missing required bar fields: {missing}")
    if execution_data_policy == "strict":
        # These fields are execution safeguards rather than strategy inputs.
        # Keep missing values visible to project-owned execution-data fill code;
        # anything still missing is rejected only when an actual order is attempted.
        for field in (
            "is_suspended",
            "limit_up",
            "limit_down",
            "raw_open",
            "raw_close",
            *(["is_st"] if has_execution_data_fill else []),
        ):
            if field not in bars:
                bars[field] = pd.NA
        # Provider-side zero/negative/non-finite limits mean unavailable data.
        # Only a validated @execution_data_fill result may use a paired zero to
        # state explicitly that an IPO session has no price limit.
        for field in ("limit_up", "limit_down"):
            numeric = pd.to_numeric(bars[field], errors="coerce")
            valid = numeric.notna() & np.isfinite(numeric) & numeric.gt(0)
            bars[field] = numeric.where(valid, pd.NA)
    sessions = tuple(pd.DatetimeIndex(bars["date"].dropna().unique()).sort_values())

    instrument_fields = list(package.get("data_requirements", {}).get("instruments") or ())
    missing_instruments = sorted(set(instrument_fields) - set(instruments.columns))
    if missing_instruments:
        raise MissingDataError(
            f"data profile is missing required instrument fields: {missing_instruments}"
        )

    fundamental_fields = list(package.get("data_requirements", {}).get("fundamentals") or ())
    fundamentals = pd.DataFrame()
    if fundamental_fields:
        fundamentals = engine.get_fundamentals(
            list(symbols),
            fundamental_fields,
            start_quarter=f"{start.year - 1}q1",
            end_quarter=f"{end.year}q4",
            asof_date=end.strftime("%Y-%m-%d"),
            strict=False,
            use_cache=False,
        )
        missing_fundamentals = sorted(set(fundamental_fields) - set(fundamentals.columns))
        if missing_fundamentals:
            raise MissingDataError(
                f"data profile is missing required fundamental fields: {missing_fundamentals}"
            )

    daily_factor_fields = list(package.get("data_requirements", {}).get("daily_factors") or ())
    daily_factors = pd.DataFrame()
    if daily_factor_fields:
        daily_factors = engine.get_daily_factors(
            list(symbols),
            daily_factor_fields,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
            strict=False,
            use_cache=False,
        )
        available_daily_fields = (
            set(daily_factors["field"].dropna().astype(str)) if "field" in daily_factors else set()
        )
        missing_daily_factors = sorted(set(daily_factor_fields) - available_daily_fields)
        if missing_daily_factors:
            raise MissingDataError(
                f"data profile is missing required daily factors: {missing_daily_factors}"
            )

    requested_indexes = list(package.get("data_requirements", {}).get("index_components") or ())
    index_components = pd.DataFrame()
    if requested_indexes:
        index_components = engine.get_index_components(
            requested_indexes,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
            strict=False,
            use_cache=False,
        )
        available_indexes = (
            set(index_components["index_symbol"].dropna().astype(str).str.upper())
            if "index_symbol" in index_components
            else set()
        )
        missing_indexes = sorted(
            set(str(value).upper() for value in requested_indexes) - available_indexes
        )
        if missing_indexes:
            raise MissingDataError(
                f"data profile is missing required index components: {missing_indexes}"
            )
    return PreparedRunData(
        sessions=sessions,
        bars=bars,
        instruments=instruments,
        fundamentals=fundamentals,
        daily_factors=daily_factors,
        index_components=index_components,
        all_symbols=symbols,
    )


def _static_payload(prepared: PreparedRunData) -> dict[str, Any]:
    return {
        "sessions": prepared.sessions,
        "bars": prepared.bars,
        "instruments": prepared.instruments,
        "fundamentals": prepared.fundamentals,
        "daily_factors": prepared.daily_factors,
        "index_components": prepared.index_components,
    }


def _rows_on(bars: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    if bars.empty or "date" not in bars:
        return pd.DataFrame()
    return bars.loc[pd.to_datetime(bars["date"]).eq(pd.Timestamp(as_of))]


def _available_symbols(
    instruments: pd.DataFrame,
    as_of: pd.Timestamp,
    market_rows: pd.DataFrame | None = None,
) -> list[str]:
    if instruments.empty or "symbol" not in instruments:
        return []
    market_symbols: set[str] | None = None
    if market_rows is not None:
        if market_rows.empty:
            return []
        rows = (
            market_rows.reset_index()
            if "symbol" not in market_rows and market_rows.index.name == "symbol"
            else market_rows
        )
        if "symbol" not in rows:
            return []
        valid = pd.Series(True, index=rows.index)
        for field in ("close", "volume"):
            if field not in rows:
                return []
            values = pd.to_numeric(rows[field], errors="coerce")
            valid &= values.notna() & np.isfinite(values) & values.gt(0)
        market_symbols = set(rows.loc[valid, "symbol"].dropna().astype(str).str.upper().unique())
        if not market_symbols:
            return []

    # Market rows reduce a large instrument master to the small set that can
    # actually trade today before any point-in-time date work is performed.
    frame = instruments
    if market_symbols is not None:
        normalized_symbols = frame["symbol"].astype(str).str.upper()
        frame = frame.loc[normalized_symbols.isin(market_symbols)]
    if "snapshot_date" in frame:
        snapshot = frame["snapshot_date"]
        if not pd.api.types.is_datetime64_any_dtype(snapshot):
            snapshot = pd.to_datetime(snapshot, errors="coerce")
        frame = frame.loc[snapshot.notna() & snapshot.le(as_of)]
        if frame.empty:
            return []
        frame = frame.loc[snapshot.loc[frame.index].eq(snapshot.loc[frame.index].max())]
    for field in ("listed_date", "list_date"):
        if field in frame:
            listed = frame[field]
            if not pd.api.types.is_datetime64_any_dtype(listed):
                listed = pd.to_datetime(listed, errors="coerce")
            frame = frame.loc[listed.isna() | listed.le(as_of)]
            break
    for field in ("delisted_date", "de_listed_date"):
        if field in frame:
            delisted = frame[field]
            if not pd.api.types.is_datetime64_any_dtype(delisted):
                delisted = pd.to_datetime(delisted, errors="coerce")
            frame = frame.loc[delisted.isna() | delisted.gt(as_of)]
            break
    symbols = set(frame["symbol"].dropna().astype(str).str.upper().unique())
    return sorted(symbols)


def _execution_data_gaps(
    rows: pd.DataFrame,
    symbols: Sequence[str],
    *,
    execution_field: str,
) -> dict[str, tuple[str, ...]]:
    """Return missing strict-execution fields for otherwise valid candidates."""

    if not symbols:
        return {}
    if rows.empty:
        return {str(symbol).upper(): ("market_row",) for symbol in symbols}
    frame = rows.reset_index() if rows.index.name == "symbol" else rows.copy()
    if "symbol" not in frame:
        return {str(symbol).upper(): ("market_row",) for symbol in symbols}
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    indexed = frame.drop_duplicates("symbol", keep="last").set_index("symbol")
    gaps: dict[str, tuple[str, ...]] = {}
    for raw_symbol in symbols:
        symbol = str(raw_symbol).upper()
        if symbol not in indexed.index:
            gaps[symbol] = ("market_row",)
            continue
        row = indexed.loc[symbol]
        missing_fields: list[str] = []
        if pd.isna(row.get("is_suspended", pd.NA)):
            missing_fields.append("is_suspended")
        limit_values = {
            field: pd.to_numeric(pd.Series([row.get(field)]), errors="coerce").iloc[0]
            for field in ("limit_up", "limit_down")
        }
        no_price_limit = all(
            pd.notna(value) and math.isfinite(float(value)) and float(value) == 0.0
            for value in limit_values.values()
        )
        if not no_price_limit:
            raw_price_field = f"raw_{execution_field}"
            raw_price = pd.to_numeric(pd.Series([row.get(raw_price_field)]), errors="coerce").iloc[
                0
            ]
            if pd.isna(raw_price) or not math.isfinite(float(raw_price)) or float(raw_price) <= 0:
                missing_fields.append(raw_price_field)
            for field, value in limit_values.items():
                if pd.isna(value) or not math.isfinite(float(value)) or float(value) <= 0:
                    missing_fields.append(field)
        if missing_fields:
            gaps[symbol] = tuple(missing_fields)
    return gaps


def _limits(project: Mapping[str, Any]) -> dict[str, float]:
    settings = project.get("settings") or {}
    return {
        "max_weight": float(settings.get("max_weight", 1.0)),
        "max_gross_exposure": float(settings.get("max_gross_exposure", 1.0)),
    }


def _event_request(
    project: Mapping[str, Any],
    as_of: pd.Timestamp,
    available: Sequence[str],
    *,
    event: str,
    portfolio: Mapping[str, Any],
    state: Mapping[str, Any],
    last_decision: Mapping[str, Any] | None,
    force_signal: bool = False,
    seed: int = 0,
) -> dict[str, Any]:
    return {
        "event": event,
        "as_of": as_of,
        "available_symbols": list(available),
        "portfolio": dict(portfolio),
        "state": dict(state),
        "last_decision": dict(last_decision) if last_decision else None,
        "force_signal": force_signal,
        "seed": seed,
        "limits": _limits(project),
    }


def _run_event(
    session: SdkExecutionSession,
    project: Mapping[str, Any],
    as_of: pd.Timestamp,
    available: Sequence[str],
    event: str,
    asset_values: Mapping[str, float],
    cash_value: float,
    entry_prices: Mapping[str, float],
    prices: Mapping[str, float],
    state: Mapping[str, Any],
    last_decision: Mapping[str, Any] | None,
    seed: int,
    *,
    include_value: bool = False,
):
    nav = cash_value + sum(asset_values.values())
    positions = []
    for symbol, value in asset_values.items():
        if value <= 1e-12:
            continue
        close = prices.get(symbol)
        entry = entry_prices.get(symbol)
        return_since_entry = (
            float(close / entry - 1.0)
            if close is not None and entry is not None and entry > 0
            else 0.0
        )
        positions.append(
            {
                "symbol": symbol,
                "weight": float(value / nav),
                "close": close,
                "entry_price": entry,
                "return_since_entry": return_since_entry,
            }
        )
    portfolio = {
        "positions": positions,
        "cash_weight": float(cash_value / nav),
        "equity": float(nav),
    }
    result = session.execute(
        "event",
        _event_request(
            project,
            as_of,
            available,
            event=event,
            portfolio=portfolio,
            state=state,
            last_decision=last_decision,
            seed=seed,
        ),
    )
    value = result.value
    next_state = dict(value["state"])
    decision = value.get("decision")
    next_last = dict(decision) if value.get("decision_changed") and decision else last_decision
    queued = (
        {
            "decision": dict(decision),
            "policy": dict(value["execution_policy"]),
            "decision_date": str(as_of)[:10],
        }
        if value.get("decision_changed") and decision and value.get("execution_policy")
        else None
    )
    if include_value:
        return next_state, next_last, queued, value
    return next_state, next_last, queued


def _queue_decision(
    queued: Mapping[str, Any],
    decision_date: pd.Timestamp,
    pending_open: dict[str, Any] | None,
    pending_close: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    item = {**dict(queued), "decision_date": str(decision_date)[:10]}
    activation = item["policy"]["activation"]
    if activation == "next_session_open":
        return item, pending_close
    return pending_open, item


def _dispatch_execution_events(
    session: SdkExecutionSession,
    project: Mapping[str, Any],
    as_of: pd.Timestamp,
    available: Sequence[str],
    asset_values: Mapping[str, float],
    cash_value: float,
    entry_prices: Mapping[str, float],
    prices: Mapping[str, float],
    state: Mapping[str, Any],
    last_decision: Mapping[str, Any] | None,
    seed: int,
    audit: Mapping[str, Any],
    manifest: Sequence[Mapping[str, Any]],
    sessions: Sequence[pd.Timestamp],
    pending_open: dict[str, Any] | None,
    pending_close: dict[str, Any] | None,
) -> tuple[dict[str, Any], Mapping[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    events: list[str] = []
    if float(audit.get("traded_weight", 0.0)) > 1e-12:
        events.append("fill")
    if (
        float(audit.get("traded_weight", 0.0)) <= 1e-12
        or audit.get("constrained_symbols")
        or audit.get("missing_amount_symbols")
    ):
        events.append("rejection")
    next_state = dict(state)
    next_last = last_decision
    for event in events:
        if not _event_needed(manifest, event, as_of, sessions):
            continue
        next_state, next_last, followup = _run_event(
            session,
            project,
            as_of,
            available,
            event,
            asset_values,
            cash_value,
            entry_prices,
            prices,
            next_state,
            next_last,
            seed,
        )
        if followup:
            pending_open, pending_close = _queue_decision(
                followup, as_of, pending_open, pending_close
            )
    return next_state, next_last, pending_open, pending_close


def _execute_target(
    pending: Mapping[str, Any],
    asset_values: Mapping[str, float],
    cash_value: float,
    entry_prices: Mapping[str, float],
    rows: pd.DataFrame,
    nav: float,
    *,
    field: str,
    project: Mapping[str, Any],
    strict_execution_data: bool,
    session: SdkExecutionSession,
    as_of: pd.Timestamp,
    event: str,
    execution_data_fill: bool,
) -> tuple[dict[str, float], float, dict[str, float], dict[str, Any]]:
    decision = pending["decision"]
    policy = pending["policy"]
    requested_target = {str(key): float(value) for key, value in decision["target_weights"].items()}
    current = {
        symbol: float(value / nav) for symbol, value in asset_values.items() if value > 1e-12
    }
    attempted_trade_count = sum(
        1
        for symbol in set(requested_target) | set(current)
        if abs(float(requested_target.get(symbol, 0.0)) - float(current.get(symbol, 0.0))) > 1e-12
    )
    execution_spec = ExecutionSpec(
        cost_bps=float(policy.get("commission_rate", 0.0)) * 10_000.0,
        slippage_bps=float(policy.get("slippage_rate", 0.0)) * 10_000.0,
        impact_bps=float(policy.get("impact_rate", 0.0)) * 10_000.0,
        execution_price="next_open" if field == "open" else "next_close",
        portfolio_value=float(project.get("settings", {}).get("portfolio_value", 1_000_000.0)),
        max_participation_rate=float(policy.get("max_participation_rate", 0.1)),
    )
    indexed_rows = rows.reset_index() if rows.index.name == "symbol" else rows.copy()
    if indexed_rows.empty:
        indexed_rows = pd.DataFrame(columns=["symbol", "date", "open", "close", "volume", "amount"])
    if "date" not in indexed_rows:
        indexed_rows["date"] = pd.NaT
    fill_audit: dict[str, Any] = {}
    if execution_data_fill:
        indexed_rows, fill_audit = _fill_execution_state(
            session,
            indexed_rows,
            symbols=[
                *requested_target,
                *current,
                *(policy.get("fallback_candidates") or ()),
            ],
            as_of=as_of,
            event=event,
        )
    trade_rows = indexed_rows.set_index("symbol") if "symbol" in indexed_rows else indexed_rows
    missing_execution_data: dict[str, tuple[str, ...]] = {}
    if strict_execution_data:
        for symbol in set(requested_target) | set(current):
            delta = float(requested_target.get(symbol, 0.0)) - float(current.get(symbol, 0.0))
            if abs(delta) > 1e-12:
                missing_execution_data.update(
                    _execution_data_gaps(
                        trade_rows,
                        [symbol],
                        execution_field=field,
                    )
                )
    target, fallback_routes = _route_fallbacks(
        requested_target,
        current,
        indexed_rows,
        field,
        policy.get("fallback_candidates") or (),
        strict_execution_data=strict_execution_data,
    )
    rejection_symbols: dict[str, set[str]] = {
        "market_state": set(missing_execution_data),
        "suspension": set(),
        "limit_up": set(),
        "limit_down": set(),
        "capacity": set(),
        "cash": set(),
    }
    for symbol in set(requested_target) | set(current):
        delta = float(requested_target.get(symbol, 0.0)) - float(current.get(symbol, 0.0))
        if abs(delta) <= 1e-12:
            continue
        reason = _trade_rejection_reason(
            trade_rows,
            symbol,
            field,
            side="buy" if delta > 0 else "sell",
            strict_execution_data=strict_execution_data,
        )
        if reason is not None:
            rejection_symbols[reason].add(symbol)
    for symbol in set(target) | set(current):
        delta = float(target.get(symbol, 0.0)) - float(current.get(symbol, 0.0))
        if abs(delta) <= 1e-12:
            continue
        side = "buy" if delta > 0 else "sell"
        reason = _trade_rejection_reason(
            trade_rows,
            symbol,
            field,
            side=side,
            strict_execution_data=strict_execution_data,
        )
        if reason is not None:
            already_state_blocked = symbol in set().union(
                rejection_symbols["market_state"],
                rejection_symbols["suspension"],
                rejection_symbols["limit_up"],
                rejection_symbols["limit_down"],
            )
            if reason != "capacity" or not already_state_blocked:
                rejection_symbols[reason].add(symbol)
            indexed_rows.loc[
                indexed_rows["symbol"].astype(str).str.upper().eq(symbol), "volume"
            ] = 0.0
    entry_date = (
        pd.Timestamp(indexed_rows["date"].dropna().iloc[0])
        if indexed_rows["date"].notna().any()
        else pd.NaT
    )
    executed, audit = _apply_execution_constraints(
        target,
        current,
        indexed_rows,
        entry_date,
        SimpleNamespace(execution=execution_spec),
    )
    state_blocked = set().union(
        rejection_symbols["market_state"],
        rejection_symbols["suspension"],
        rejection_symbols["limit_up"],
        rejection_symbols["limit_down"],
    )
    rejection_symbols["capacity"].update(
        set(audit.get("capacity_rejection_symbols") or ()) - state_blocked
    )
    rejection_symbols["cash"].update(audit.get("cash_rejection_symbols") or ())
    successful_trade_count = sum(
        1
        for symbol in set(executed) | set(current)
        if abs(float(executed.get(symbol, 0.0)) - float(current.get(symbol, 0.0))) > 1e-12
    )
    total_cost = float(audit["total_cost"]) * nav
    next_values = {symbol: weight * nav for symbol, weight in executed.items()}
    next_cash = nav - sum(next_values.values()) - total_cost
    if next_cash < -1e-8:
        raise ValueError("execution costs exceeded available cash")
    next_entries = dict(entry_prices)
    price_map = _prices(rows, field)
    for symbol in set(current) | set(executed):
        old = current.get(symbol, 0.0)
        new = executed.get(symbol, 0.0)
        price = price_map.get(symbol)
        if new <= 1e-12:
            next_entries.pop(symbol, None)
        elif old <= 1e-12 and price is not None:
            next_entries[symbol] = price
        elif new > old + 1e-12 and price is not None:
            old_entry = next_entries.get(symbol, price)
            next_entries[symbol] = (old * old_entry + (new - old) * price) / new
    requested_trade_weight = float(
        sum(
            abs(float(requested_target.get(symbol, 0.0)) - float(current.get(symbol, 0.0)))
            for symbol in set(requested_target) | set(current)
        )
    )
    target_weight_deviation = float(
        sum(
            abs(float(requested_target.get(symbol, 0.0)) - float(executed.get(symbol, 0.0)))
            for symbol in set(requested_target) | set(executed)
        )
    )
    execution_fidelity = (
        max(0.0, 1.0 - min(1.0, target_weight_deviation / requested_trade_weight))
        if requested_trade_weight > 1e-12
        else 1.0
    )
    exit_failure_symbols = sorted(
        symbol
        for symbol in set(current) | set(requested_target)
        if float(requested_target.get(symbol, 0.0)) < float(current.get(symbol, 0.0)) - 1e-12
        and float(executed.get(symbol, 0.0)) > float(requested_target.get(symbol, 0.0)) + 1e-6
    )
    return (
        next_values,
        max(0.0, next_cash),
        next_entries,
        {
            **audit,
            "target_weights": requested_target,
            "routed_target_weights": target,
            "fallback_routes": fallback_routes,
            "executed_weights": executed,
            "missing_execution_data": [
                {"symbol": symbol, "missing_fields": list(fields)}
                for symbol, fields in sorted(missing_execution_data.items())
            ],
            "execution_data_fill": fill_audit,
            "attempted_trade_count": attempted_trade_count,
            "successful_trade_count": successful_trade_count,
            "execution_data_fill_count": int(
                sum(int(value or 0) for value in fill_audit.get("filled", {}).values())
            ),
            "synthetic_state_count": int(fill_audit.get("synthetic_state_count") or 0),
            "market_state_rejection_count": len(rejection_symbols["market_state"]),
            "suspension_rejection_count": len(rejection_symbols["suspension"]),
            "limit_up_rejection_count": len(rejection_symbols["limit_up"]),
            "limit_down_rejection_count": len(rejection_symbols["limit_down"]),
            "capacity_rejection_count": len(rejection_symbols["capacity"]),
            "cash_rejection_count": len(rejection_symbols["cash"]),
            "rejection_symbols": {
                reason: sorted(symbols) for reason, symbols in rejection_symbols.items() if symbols
            },
            "requested_trade_weight": requested_trade_weight,
            "target_weight_deviation": target_weight_deviation,
            "execution_fidelity": execution_fidelity,
            "exit_failure_symbols": exit_failure_symbols,
            "decision_reason": decision.get("reason"),
            "state_committed": True,
        },
    )


def _compact_event_diagnostic(
    current_date: pd.Timestamp,
    event: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep event audit small while retaining the SDK-native event identity."""

    return {
        "date": str(current_date)[:10],
        "event": event,
        "signal_due": bool(value.get("signal_due", False)),
        "invoked": list(value.get("invoked") or ()),
        "state_sha256": value.get("state_sha256"),
        "decision_reason": (value.get("decision") or {}).get("reason"),
    }


def _record_signal_evidence(
    event_value: Mapping[str, Any],
    current_date: pd.Timestamp,
    prices: Mapping[str, float],
    rows: list[dict[str, Any]],
    pending: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Resolve prior-signal IC and retain only compact evidence for the new signal."""

    signal = event_value.get("signal")
    if not event_value.get("signal_due") or not isinstance(signal, Mapping):
        return pending
    raw_scores = signal.get("scores")
    scores: dict[str, float] = {}
    if isinstance(raw_scores, Mapping):
        for symbol, raw_value in raw_scores.items():
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                scores[str(symbol).upper()] = value
    selected = {
        str(symbol).upper() for symbol in signal.get("selected") or () if isinstance(symbol, str)
    }
    if pending is not None:
        prior_scores = pending["scores"]
        prior_prices = pending["prices"]
        paired = sorted(set(prior_scores) & set(prior_prices) & set(prices))
        forward_returns = {
            symbol: float(prices[symbol] / prior_prices[symbol] - 1.0)
            for symbol in paired
            if prior_prices[symbol] > 0
            and math.isfinite(float(prices[symbol]))
            and math.isfinite(float(prior_prices[symbol]))
        }
        paired = sorted(set(prior_scores) & set(forward_returns))
        correlation: float | None = None
        if len(paired) >= 3:
            ranked_scores = pd.Series({symbol: prior_scores[symbol] for symbol in paired}).rank()
            ranked_returns = pd.Series(
                {symbol: forward_returns[symbol] for symbol in paired}
            ).rank()
            value = ranked_scores.corr(ranked_returns)
            if pd.notna(value) and math.isfinite(float(value)):
                correlation = float(value)
        pending_row = pending["row"]
        pending_row["horizon_end_date"] = str(current_date)[:10]
        pending_row["ic_observations"] = len(paired)
        pending_row["ic"] = correlation

    universe = {
        str(symbol).upper()
        for symbol in event_value.get("universe") or ()
        if isinstance(symbol, str)
    }
    previous_selected = pending["selected"] if pending is not None else set()
    denominator = len(selected) + len(previous_selected)
    turnover = (
        len(selected.symmetric_difference(previous_selected)) / denominator
        if previous_selected and denominator
        else None
    )
    row = {
        "signal_date": str(current_date)[:10],
        "horizon_end_date": None,
        "universe_count": len(universe),
        "scored_count": len(scores),
        "selected_count": len(selected),
        "coverage": float(len(scores) / len(universe)) if universe else None,
        "ic": None,
        "ic_observations": 0,
        "selection_turnover": float(turnover) if turnover is not None else None,
    }
    rows.append(row)
    return {
        "scores": scores,
        "prices": {
            symbol: float(prices[symbol])
            for symbol in scores
            if symbol in prices and prices[symbol] > 0 and math.isfinite(float(prices[symbol]))
        },
        "selected": selected,
        "row": row,
    }


def _fill_execution_state(
    session: SdkExecutionSession,
    rows: pd.DataFrame,
    *,
    symbols: Sequence[str],
    as_of: pd.Timestamp,
    event: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run the project's visible state-fill Python for actual order candidates."""

    normalized_symbols = sorted({str(symbol).strip().upper() for symbol in symbols})
    if rows.empty or not normalized_symbols or "symbol" not in rows:
        return rows, {}
    output = rows.copy()
    output["symbol"] = output["symbol"].astype(str).str.upper()
    state_rows = output.loc[output["symbol"].isin(normalized_symbols)].copy()
    if state_rows.empty:
        return output, {}
    state_fields = ("is_suspended", "limit_up", "limit_down")
    for field in state_fields:
        if field not in state_rows:
            state_rows[field] = pd.NA
    provider_counts = {field: int(state_rows[field].notna().sum()) for field in state_fields}
    request_rows = state_rows[["date", "symbol", *state_fields]].to_dict(orient="records")
    result = session.execute(
        "execution_data_fill",
        {
            "event": event,
            "as_of": as_of,
            "available_symbols": normalized_symbols,
            "execution_state_rows": request_rows,
        },
    ).value
    filled = pd.DataFrame(result.get("rows") or ())
    if filled.empty:
        return output, {}
    filled["symbol"] = filled["symbol"].astype(str).str.upper()
    filled = filled.drop_duplicates("symbol", keep="last").set_index("symbol")
    filled_values: list[dict[str, str]] = []
    strategy_fill_counts: dict[str, int] = {}
    for field in state_fields:
        if field not in output:
            output[field] = pd.NA
        replacement = output["symbol"].map(filled[field])
        missing = output[field].isna() & replacement.notna()
        output.loc[missing, field] = replacement.loc[missing]
        count = int(missing.sum())
        if count:
            strategy_fill_counts[field] = count
            filled_values.extend(
                {
                    "symbol": str(symbol),
                    "field": field,
                    "source": "strategy_fill",
                }
                for symbol in output.loc[missing, "symbol"].tolist()
            )
    synthetic_state_count = sum(strategy_fill_counts.values())
    return output, {
        "entrypoint_id": result.get("entrypoint_id"),
        "filled": strategy_fill_counts,
        "synthetic_state_count": synthetic_state_count,
        "source_counts": {
            "provider": provider_counts,
            "strategy_fill": strategy_fill_counts,
            "fallback": {},
        },
        "filled_values": filled_values,
    }


def _record_execution_data_audit(
    audit: Mapping[str, Any],
    current_date: pd.Timestamp,
    exclusion_symbols: set[str],
    exclusion_samples: list[dict[str, Any]],
    fill_count: int,
    fill_fields: Mapping[str, int],
) -> tuple[int, dict[str, int]]:
    for item in audit.get("missing_execution_data") or ():
        symbol = str(item.get("symbol") or "").upper()
        if symbol:
            exclusion_symbols.add(symbol)
        if len(exclusion_samples) < 50:
            exclusion_samples.append(
                {
                    "date": str(current_date)[:10],
                    "symbol": symbol,
                    "missing_fields": list(item.get("missing_fields") or ()),
                }
            )
    next_fields = dict(fill_fields)
    filled = (audit.get("execution_data_fill") or {}).get("filled") or {}
    for field, raw_count in filled.items():
        count = int(raw_count)
        next_fields[str(field)] = next_fields.get(str(field), 0) + count
        fill_count += count
    return fill_count, next_fields


def _route_fallbacks(
    target: Mapping[str, float],
    current: Mapping[str, float],
    rows: pd.DataFrame,
    field: str,
    fallbacks: Sequence[str],
    *,
    strict_execution_data: bool,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    routed = dict(target)
    routes: list[dict[str, Any]] = []
    indexed = rows.set_index("symbol") if "symbol" in rows else rows
    used = set(routed) | set(current)
    for symbol, wanted in list(target.items()):
        old = float(current.get(symbol, 0.0))
        increase = float(wanted) - old
        if increase <= 1e-12 or _trade_allowed(
            indexed,
            symbol,
            field,
            side="buy",
            strict_execution_data=strict_execution_data,
        ):
            continue
        routed[symbol] = old
        if routed[symbol] <= 1e-12:
            routed.pop(symbol, None)
        replacement = next(
            (
                str(candidate).upper()
                for candidate in fallbacks
                if str(candidate).upper() not in used
                and _trade_allowed(
                    indexed,
                    str(candidate).upper(),
                    field,
                    side="buy",
                    strict_execution_data=strict_execution_data,
                )
            ),
            None,
        )
        if replacement is None:
            continue
        routed[replacement] = (
            float(routed.get(replacement, current.get(replacement, 0.0))) + increase
        )
        used.add(replacement)
        routes.append({"from": symbol, "to": replacement, "weight": increase})
    return routed, routes


def _trade_allowed(
    rows: pd.DataFrame,
    symbol: str,
    field: str,
    *,
    side: str,
    strict_execution_data: bool = False,
) -> bool:
    return (
        _trade_rejection_reason(
            rows,
            symbol,
            field,
            side=side,
            strict_execution_data=strict_execution_data,
        )
        is None
    )


def _trade_rejection_reason(
    rows: pd.DataFrame,
    symbol: str,
    field: str,
    *,
    side: str,
    strict_execution_data: bool = False,
) -> str | None:
    if symbol not in rows.index:
        return "market_state" if strict_execution_data else "capacity"
    row = rows.loc[symbol]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    suspended = row.get("is_suspended", pd.NA)
    if strict_execution_data and pd.isna(suspended):
        return "market_state"
    if pd.notna(suspended) and bool(suspended):
        return "suspension"
    limit_values = {
        name: pd.to_numeric(pd.Series([row.get(name)]), errors="coerce").iloc[0]
        for name in ("limit_up", "limit_down")
    }
    no_price_limit = all(
        pd.notna(value) and math.isfinite(float(value)) and float(value) == 0.0
        for value in limit_values.values()
    )
    single_zero = (
        any(
            pd.notna(value) and math.isfinite(float(value)) and float(value) == 0.0
            for value in limit_values.values()
        )
        and not no_price_limit
    )
    limit_field = "limit_up" if side == "buy" else "limit_down"
    limit_value = limit_values[limit_field]
    if strict_execution_data and not no_price_limit:
        if single_zero or (
            pd.isna(limit_value) or not math.isfinite(float(limit_value)) or float(limit_value) <= 0
        ):
            return "market_state"
    if not no_price_limit and pd.notna(limit_value) and float(limit_value) > 0:
        raw_price_field = f"raw_{field}"
        raw_price = pd.to_numeric(pd.Series([row.get(raw_price_field)]), errors="coerce").iloc[0]
        if pd.isna(raw_price) or not math.isfinite(float(raw_price)) or float(raw_price) <= 0:
            return "market_state" if strict_execution_data else None
        if side == "buy" and float(raw_price) >= float(limit_value) - 1e-12:
            return "limit_up"
        if side == "sell" and float(raw_price) <= float(limit_value) + 1e-12:
            return "limit_down"
    price = pd.to_numeric(pd.Series([row.get(field)]), errors="coerce").iloc[0]
    volume = pd.to_numeric(pd.Series([row.get("volume")]), errors="coerce").iloc[0]
    amount = pd.to_numeric(pd.Series([row.get("amount")]), errors="coerce").iloc[0]
    if any(
        pd.isna(value) or not math.isfinite(float(value)) or float(value) <= 0
        for value in (price, volume, amount)
    ):
        return "capacity"
    return None


def _settle_delisted_positions(
    asset_values: dict[str, float],
    entry_prices: dict[str, float],
    previous_close_prices: dict[str, float],
    instruments: pd.DataFrame,
    as_of: pd.Timestamp,
) -> list[dict[str, Any]]:
    """Write held instruments off at zero on their delisting date."""

    held = {str(symbol).upper() for symbol, value in asset_values.items() if value > 1e-12}
    if not held or instruments.empty or "symbol" not in instruments:
        return []
    delisted_field = next(
        (field for field in ("de_listed_date", "delisted_date") if field in instruments),
        None,
    )
    if delisted_field is None:
        return []
    frame = instruments.loc[
        instruments["symbol"].astype(str).str.upper().isin(held),
        ["symbol", delisted_field],
    ].copy()
    delisted = pd.to_datetime(frame[delisted_field], errors="coerce")
    affected = sorted(
        frame.loc[delisted.notna() & delisted.le(pd.Timestamp(as_of)), "symbol"]
        .astype(str)
        .str.upper()
        .unique()
    )
    settled = []
    for symbol in affected:
        settled.append(
            {
                "symbol": symbol,
                "written_off_value": float(asset_values.pop(symbol, 0.0)),
            }
        )
        entry_prices.pop(symbol, None)
        previous_close_prices.pop(symbol, None)
    return settled


def _prices(rows: pd.DataFrame, field: str) -> dict[str, float]:
    if rows.empty or field not in rows:
        return {}
    frame = rows
    if "symbol" in frame.columns:
        frame = frame.set_index("symbol")
    result = {}
    for symbol, value in pd.to_numeric(frame[field], errors="coerce").items():
        if pd.notna(value) and math.isfinite(float(value)) and float(value) > 0:
            result[str(symbol).upper()] = float(value)
    return result


def _evaluation_dates(
    sessions: Sequence[pd.Timestamp],
    start: pd.Timestamp,
    end: pd.Timestamp,
    frequency: str,
) -> list[pd.Timestamp]:
    selected = pd.DatetimeIndex([value for value in sessions if start <= value <= end])
    if frequency == "daily":
        return [pd.Timestamp(value) for value in selected]
    series = pd.Series(selected, index=selected)
    periods = selected.to_period("W-FRI" if frequency == "weekly" else "M")
    return [pd.Timestamp(value) for value in series.groupby(periods).max()]


def _factor_input_audit(
    prepared: PreparedRunData, as_of: pd.Timestamp
) -> dict[str, str | None]:
    """Record the latest input timestamps visible to one factor evaluation."""

    factor_dates: list[pd.Timestamp] = []
    for frame, candidates in (
        (prepared.bars, ("date",)),
        (prepared.daily_factors, ("date", "available_date")),
    ):
        value = _latest_visible_date(frame, candidates, as_of)
        if value is not None:
            factor_dates.append(value)
    factor_max = max(factor_dates) if factor_dates else None
    return {
        "factor_input_max_date": _date_string(factor_max),
        "security_snapshot_max_date": _date_string(
            _latest_visible_date(prepared.instruments, ("snapshot_date",), as_of)
        ),
        "fundamental_available_max_date": _date_string(
            _latest_visible_date(prepared.fundamentals, ("available_date",), as_of)
        ),
    }


def _latest_visible_date(
    frame: pd.DataFrame,
    candidates: Sequence[str],
    as_of: pd.Timestamp,
) -> pd.Timestamp | None:
    if frame.empty:
        return None
    field = next((name for name in candidates if name in frame), None)
    if field is None:
        return None
    values = pd.to_datetime(frame[field], errors="coerce").dropna()
    visible = values.loc[values.le(pd.Timestamp(as_of))]
    return pd.Timestamp(visible.max()).normalize() if not visible.empty else None


def _date_string(value: pd.Timestamp | None) -> str | None:
    return value.strftime("%Y-%m-%d") if value is not None else None


def _event_needed(
    manifest: Sequence[Mapping[str, Any]],
    event: str,
    session: pd.Timestamp,
    sessions: Sequence[pd.Timestamp],
) -> bool:
    if any(item.get("kind") == "event" and item.get("event") == event for item in manifest):
        return True
    if event not in {"session_open", "session_close"}:
        return False
    signal = next((item for item in manifest if item.get("kind") == "signal"), {})
    schedule = dict(signal.get("metadata", {}).get("schedule") or {})
    if schedule.get("mode") != "structured":
        return True
    expected_event = "session_open" if schedule.get("at") == "open" else "session_close"
    if event != expected_event:
        return False
    frequency = schedule.get("frequency")
    if frequency == "daily":
        return True
    calendar = pd.DatetimeIndex(sessions)
    position = int(calendar.searchsorted(session))
    previous = calendar[position - 1] if position > 0 else None
    following = calendar[position + 1] if position + 1 < len(calendar) else None
    selector = schedule.get("selector")
    if frequency == "monthly":
        if selector == "first_trading_day":
            return previous is None or previous.month != session.month
        return following is None or following.month != session.month
    if selector == "first_trading_day":
        return previous is None or previous.isocalendar().week != session.isocalendar().week
    return following is None or following.isocalendar().week != session.isocalendar().week


def _apply_execution_constraints(
    target: dict[str, float],
    current: dict[str, float],
    bars: pd.DataFrame,
    entry_date: pd.Timestamp,
    config: Any,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Apply the SDK engine's liquidity, cash, and cost constraints."""

    entry_rows = (
        bars.loc[bars["date"].eq(entry_date)]
        .drop_duplicates("symbol", keep="last")
        .set_index("symbol")
    )
    field = "open" if config.execution.execution_price == "next_open" else "close"
    desired: dict[str, float] = {}
    constrained: set[str] = set()
    missing_amount: set[str] = set()
    capacity_rejections: set[str] = set()
    cash_rejections: set[str] = set()
    participation: dict[str, float] = {}
    universe = set(target) | set(current)
    for symbol in universe:
        old = float(current.get(symbol, 0.0))
        wanted = float(target.get(symbol, 0.0))
        row = entry_rows.loc[symbol] if symbol in entry_rows.index else None
        price = pd.to_numeric(
            pd.Series([row.get(field) if row is not None else None]), errors="coerce"
        ).iloc[0]
        volume = pd.to_numeric(
            pd.Series([row.get("volume") if row is not None else None]),
            errors="coerce",
        ).iloc[0]
        if pd.isna(price) or float(price) <= 0 or pd.isna(volume) or float(volume) <= 0:
            desired[symbol] = old
            if abs(wanted - old) > 1e-12:
                constrained.add(symbol)
                capacity_rejections.add(symbol)
            continue
        delta = wanted - old
        amount = pd.to_numeric(
            pd.Series([row.get("amount") if row is not None else None]),
            errors="coerce",
        ).iloc[0]
        if pd.notna(amount) and float(amount) > 0:
            capacity = (
                float(amount)
                * config.execution.max_participation_rate
                / config.execution.portfolio_value
            )
            if abs(delta) > capacity:
                delta = float(np.sign(delta) * capacity)
                constrained.add(symbol)
                capacity_rejections.add(symbol)
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
                capacity_rejections.add(symbol)
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
    for symbol in sorted(
        universe,
        key=lambda value: (
            -(desired.get(value, 0.0) - current.get(value, 0.0)),
            str(value),
        ),
    ):
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
            cash_rejections.add(symbol)
    executed = {symbol: float(weight) for symbol, weight in executed.items() if weight > 1e-12}
    deltas = {
        symbol: executed.get(symbol, 0.0) - current.get(symbol, 0.0)
        for symbol in set(executed) | set(current)
    }
    traded_weight = float(sum(abs(value) for value in deltas.values()))
    previous_cash = max(0.0, 1.0 - sum(current.values()))
    next_cash = max(0.0, 1.0 - sum(executed.values()))
    turnover = 0.5 * (traded_weight + abs(next_cash - previous_cash))
    costs = _execution_cost(deltas, participation, config)
    return executed, {
        "turnover": float(turnover),
        "traded_weight": traded_weight,
        "fixed_cost": float(costs["fixed_cost"]),
        "impact_cost": float(costs["impact_cost"]),
        "total_cost": costs["total_cost"],
        "target_count": len(target),
        "executed_count": len(executed),
        "constrained_symbols": sorted(constrained),
        "missing_amount_symbols": sorted(missing_amount),
        "capacity_rejection_symbols": sorted(capacity_rejections),
        "cash_rejection_symbols": sorted(cash_rejections),
    }


def _aggregate_execution_summary(executions: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    summary = _empty_execution_summary()
    for execution in executions:
        for field in _EXECUTION_COUNTER_FIELDS:
            summary[field] += int(execution.get(field) or 0)
    return summary


def _aggregate_execution_state_sources(
    executions: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {
        "provider": {},
        "strategy_fill": {},
        "fallback": {},
    }
    for execution in executions:
        source_counts = (execution.get("execution_data_fill") or {}).get("source_counts") or {}
        for source, fields in source_counts.items():
            target = totals.setdefault(str(source), {})
            for field, value in dict(fields or {}).items():
                target[str(field)] = target.get(str(field), 0) + int(value or 0)
    return {source: dict(sorted(fields.items())) for source, fields in totals.items()}


def _execution_fidelity_summary(executions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    attempted = [item for item in executions if int(item.get("attempted_trade_count") or 0)]
    fidelities = [float(item.get("execution_fidelity", 1.0)) for item in attempted]
    deviations = [float(item.get("target_weight_deviation") or 0.0) for item in attempted]
    low_fidelity_period_count = sum(value < 0.8 for value in fidelities)
    tracking_streak = 0
    maximum_tracking_streak = 0
    exit_streaks: dict[str, int] = {}
    maximum_exit_streaks: dict[str, int] = {}
    for item in attempted:
        deviation = float(item.get("target_weight_deviation") or 0.0)
        tracking_streak = tracking_streak + 1 if deviation > 0.05 else 0
        maximum_tracking_streak = max(maximum_tracking_streak, tracking_streak)
        failed = {str(symbol).upper() for symbol in item.get("exit_failure_symbols") or ()}
        for symbol in set(exit_streaks) | failed:
            exit_streaks[symbol] = exit_streaks.get(symbol, 0) + 1 if symbol in failed else 0
            maximum_exit_streaks[symbol] = max(
                maximum_exit_streaks.get(symbol, 0), exit_streaks[symbol]
            )
    persistent_exit_symbols = sorted(
        symbol for symbol, streak in maximum_exit_streaks.items() if streak >= 2
    )
    return {
        "mean": float(np.mean(fidelities)) if fidelities else None,
        "minimum": min(fidelities) if fidelities else None,
        "low_fidelity_period_count": low_fidelity_period_count,
        "persistent_tracking_error_periods": maximum_tracking_streak,
        "maximum_target_weight_deviation": max(deviations, default=0.0),
        "persistent_exit_failure_symbols": persistent_exit_symbols[:50],
    }


def _execution_cost_rate(
    symbol: str,
    participation: dict[str, float],
    config: Any,
) -> float:
    fixed = (config.execution.cost_bps + config.execution.slippage_bps) / 10_000.0
    impact = (config.execution.impact_bps / 10_000.0) * np.sqrt(
        min(
            1.0,
            participation.get(symbol, 0.0) / config.execution.max_participation_rate,
        )
    )
    return float(fixed + impact)


def _execution_cost(
    deltas: dict[str, float],
    participation: dict[str, float],
    config: Any,
) -> dict[str, float]:
    fixed_rate = (config.execution.cost_bps + config.execution.slippage_bps) / 10_000.0
    fixed_cost = sum(abs(delta) for delta in deltas.values()) * fixed_rate
    impact_cost = sum(
        abs(delta) * max(0.0, _execution_cost_rate(symbol, participation, config) - fixed_rate)
        for symbol, delta in deltas.items()
    )
    return {
        "fixed_cost": float(fixed_cost),
        "impact_cost": float(impact_cost),
        "total_cost": float(fixed_cost + impact_cost),
    }


__all__ = [
    "PreparedRunData",
    "StrategyBacktestResult",
    "evaluate_factor_history",
    "evaluate_factor_research",
    "evaluate_factor_snapshot",
    "preview_strategy",
    "run_strategy_backtest",
]
