"""One event-driven research and backtest engine for Strategy SDK v1."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from alphalab.dataio import DataEngine, MissingDataError
from alphalab.strategy.config import ExecutionSpec
from alphalab.strategy.repository import StrategyRepository
from alphalab.strategy.sdk_runtime import SdkExecutionSession


@dataclass(frozen=True)
class StrategyBacktestResult:
    project: dict[str, Any]
    package: dict[str, Any]
    returns: pd.Series
    weights: pd.DataFrame
    executions: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class PreparedRunData:
    sessions: tuple[pd.Timestamp, ...]
    bars: pd.DataFrame
    instruments: pd.DataFrame
    fundamentals: pd.DataFrame
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


def run_strategy_backtest(
    repository: StrategyRepository,
    project_id: str,
    start_date: str,
    end_date: str,
    data_engine: DataEngine,
    *,
    revision: int | None = None,
    seed: int = 0,
) -> StrategyBacktestResult:
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
    sessions = tuple(value for value in prepared.sessions if start <= value <= end)
    if len(sessions) < 2:
        return StrategyBacktestResult(
            project=project,
            package=package,
            returns=pd.Series(dtype=float, name=project_id),
            weights=pd.DataFrame(),
            executions=(),
            diagnostics={
                "sdk_version": 1,
                "source_sha256": package["source_sha256"],
                "revision": package["revision"],
                "warnings": ["insufficient sessions"],
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
    warnings: set[str] = set()
    run_manifest = list(package.get("manifest") or [])

    with SdkExecutionSession(package["source"], timeout_seconds=30.0) as session:
        session.configure(_static_payload(prepared))
        for session_index, current_date in enumerate(sessions):
            rows = bars_by_date.get(current_date, pd.DataFrame())
            available = _available_symbols(prepared.instruments, current_date, rows)

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
                )
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
                state, last_decision, open_decision = _run_event(
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
                )
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
                    {
                        "date": str(current_date)[:10],
                        "signal_due": event_value.get("signal_due", False),
                        "invoked": event_value.get("invoked", []),
                        "state_sha256": event_value.get("state_sha256"),
                        "decision_reason": (event_value.get("decision") or {}).get("reason"),
                    }
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
    return StrategyBacktestResult(
        project=project,
        package=package,
        returns=returns_series,
        weights=weights_frame,
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
            "final_state": state,
            "warnings": sorted(warnings),
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
) -> PreparedRunData:
    end_instruments = engine.get_instruments(end.strftime("%Y-%m-%d"))
    if end_instruments.empty or "symbol" not in end_instruments:
        raise MissingDataError("point-in-time instrument snapshots are required")
    symbols = tuple(sorted(end_instruments["symbol"].dropna().astype(str).str.upper().unique()))
    fields = list(
        dict.fromkeys(
            package.get("data_requirements", {}).get("bars")
            or ["open", "high", "low", "close", "volume", "amount"]
        )
    )
    required_execution_fields = ["open", "close", "volume", "amount"]
    fields = list(dict.fromkeys([*fields, *required_execution_fields]))
    bars = engine.get_bars(
        list(symbols),
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
        fields=fields,
        strict=False,
        use_cache=False,
    )
    if bars.empty:
        raise MissingDataError("no market bars for the selected StrategySourcePackage")
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"])
    missing = sorted(set(fields) - set(bars.columns))
    if missing:
        raise MissingDataError(f"data profile is missing required bar fields: {missing}")
    sessions = tuple(pd.DatetimeIndex(bars["date"].dropna().unique()).sort_values())

    instrument_frames: list[pd.DataFrame] = []
    snapshot_keys: set[str] = set()
    for session in sessions:
        frame = engine.get_instruments(session.strftime("%Y-%m-%d"))
        if frame.empty:
            continue
        frame = frame.copy()
        if "snapshot_date" not in frame:
            frame["snapshot_date"] = session
        key = str(pd.to_datetime(frame["snapshot_date"], errors="coerce").max())
        if key not in snapshot_keys:
            snapshot_keys.add(key)
            instrument_frames.append(frame)
    instruments = (
        pd.concat(instrument_frames, ignore_index=True) if instrument_frames else pd.DataFrame()
    )
    if instruments.empty:
        raise MissingDataError("point-in-time instrument snapshots are required")
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
    return PreparedRunData(
        sessions=sessions,
        bars=bars,
        instruments=instruments,
        fundamentals=fundamentals,
        all_symbols=symbols,
    )


def _static_payload(prepared: PreparedRunData) -> dict[str, Any]:
    return {
        "sessions": prepared.sessions,
        "bars": prepared.bars,
        "instruments": prepared.instruments,
        "fundamentals": prepared.fundamentals,
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
    frame = instruments.copy()
    if "snapshot_date" in frame:
        frame["snapshot_date"] = pd.to_datetime(frame["snapshot_date"], errors="coerce")
        frame = frame.loc[frame["snapshot_date"].notna() & frame["snapshot_date"].le(as_of)]
        if frame.empty:
            return []
        frame = frame.loc[frame["snapshot_date"].eq(frame["snapshot_date"].max())]
    for field in ("listed_date", "list_date"):
        if field in frame:
            listed = pd.to_datetime(frame[field], errors="coerce")
            frame = frame.loc[listed.isna() | listed.le(as_of)]
            break
    for field in ("delisted_date", "de_listed_date"):
        if field in frame:
            delisted = pd.to_datetime(frame[field], errors="coerce")
            frame = frame.loc[delisted.isna() | delisted.gt(as_of)]
            break
    symbols = set(frame["symbol"].dropna().astype(str).str.upper().unique())
    if market_rows is not None:
        if market_rows.empty:
            return []
        rows = (
            market_rows.reset_index()
            if "symbol" not in market_rows and market_rows.index.name == "symbol"
            else market_rows.copy()
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
        symbols &= market_symbols
    return sorted(symbols)


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
) -> tuple[dict[str, float], float, dict[str, float], dict[str, Any]]:
    decision = pending["decision"]
    policy = pending["policy"]
    requested_target = {str(key): float(value) for key, value in decision["target_weights"].items()}
    current = {
        symbol: float(value / nav) for symbol, value in asset_values.items() if value > 1e-12
    }
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
    target, fallback_routes = _route_fallbacks(
        requested_target,
        current,
        indexed_rows,
        field,
        policy.get("fallback_candidates") or (),
    )
    trade_rows = indexed_rows.set_index("symbol") if "symbol" in indexed_rows else indexed_rows
    for symbol in set(target) | set(current):
        delta = float(target.get(symbol, 0.0)) - float(current.get(symbol, 0.0))
        if abs(delta) <= 1e-12:
            continue
        side = "buy" if delta > 0 else "sell"
        if not _trade_allowed(trade_rows, symbol, field, side=side):
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
            "decision_reason": decision.get("reason"),
            "state_committed": True,
        },
    )


def _route_fallbacks(
    target: Mapping[str, float],
    current: Mapping[str, float],
    rows: pd.DataFrame,
    field: str,
    fallbacks: Sequence[str],
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    routed = dict(target)
    routes: list[dict[str, Any]] = []
    indexed = rows.set_index("symbol") if "symbol" in rows else rows
    used = set(routed) | set(current)
    for symbol, wanted in list(target.items()):
        old = float(current.get(symbol, 0.0))
        increase = float(wanted) - old
        if increase <= 1e-12 or _trade_allowed(indexed, symbol, field, side="buy"):
            continue
        routed[symbol] = old
        if routed[symbol] <= 1e-12:
            routed.pop(symbol, None)
        replacement = next(
            (
                str(candidate).upper()
                for candidate in fallbacks
                if str(candidate).upper() not in used
                and _trade_allowed(indexed, str(candidate).upper(), field, side="buy")
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
) -> bool:
    if symbol not in rows.index:
        return False
    row = rows.loc[symbol]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    price = pd.to_numeric(pd.Series([row.get(field)]), errors="coerce").iloc[0]
    volume = pd.to_numeric(pd.Series([row.get("volume")]), errors="coerce").iloc[0]
    amount = pd.to_numeric(pd.Series([row.get("amount")]), errors="coerce").iloc[0]
    if any(
        pd.isna(value) or not math.isfinite(float(value)) or float(value) <= 0
        for value in (price, volume, amount)
    ):
        return False
    if bool(row.get("is_suspended", False)):
        return False
    limit_field = "limit_up" if side == "buy" else "limit_down"
    limit_value = pd.to_numeric(pd.Series([row.get(limit_field)]), errors="coerce").iloc[0]
    if pd.notna(limit_value) and float(limit_value) > 0:
        if side == "buy" and float(price) >= float(limit_value) - 1e-12:
            return False
        if side == "sell" and float(price) <= float(limit_value) + 1e-12:
            return False
    return True


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
    for symbol in sorted(
        universe,
        key=lambda value: desired.get(value, 0.0) - current.get(value, 0.0),
        reverse=True,
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
        if available_cash <= 1e-12:
            break
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
    "evaluate_factor_snapshot",
    "preview_strategy",
    "run_strategy_backtest",
]
