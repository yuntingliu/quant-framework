"""Timeout-bounded local process runner for canonical Strategy SDK v1 modules."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import inspect
import io
import math
import multiprocessing as mp
import traceback
from dataclasses import asdict, dataclass
from multiprocessing.connection import Connection
from types import MappingProxyType
from typing import Any, Callable, Mapping

import pandas as pd
from packaging.specifiers import InvalidSpecifier, SpecifierSet

from alphalab.sdk.v1.decorators import Registration
from alphalab.sdk.v1.model import (
    Event,
    ExecutionContext,
    ExecutionPolicy,
    FactorContext,
    Holding,
    PortfolioContext,
    PortfolioDecision,
    PortfolioSnapshot,
    Schedule,
    SignalContext,
    SignalResult,
    State,
    StrategyContext,
    UniverseContext,
    UniverseResult,
    schedule_is_due,
    state_after,
)
from alphalab.strategy.source import SourceInspection, inspect_strategy_source


MAX_LOG_CHARS = 20_000


class SdkRuntimeError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        phase: str = "execute",
        entrypoint_id: str | None = None,
        event: str | None = None,
        as_of: str | None = None,
        source_sha256: str | None = None,
        committed: bool = False,
        traceback_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.entrypoint_id = entrypoint_id
        self.event = event
        self.as_of = as_of
        self.source_sha256 = source_sha256
        self.committed = committed
        self.traceback_text = traceback_text

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "phase": self.phase,
            "entrypoint_id": self.entrypoint_id,
            "event": self.event,
            "as_of": self.as_of,
            "source_sha256": self.source_sha256,
            "committed": self.committed,
            "traceback": self.traceback_text,
        }


@dataclass(frozen=True)
class RegisteredCallable:
    registration: Registration
    function: Callable[..., Any]


@dataclass(frozen=True)
class LoadedStrategy:
    source_sha256: str
    inspection: SourceInspection
    by_kind: Mapping[str, tuple[RegisteredCallable, ...]]
    by_id: Mapping[str, RegisteredCallable]
    event_handlers: Mapping[Event, RegisteredCallable]

    def one(self, kind: str) -> RegisteredCallable:
        values = self.by_kind.get(kind, ())
        if len(values) != 1:
            raise SdkRuntimeError(f"runtime registry requires one {kind}", phase="register")
        return values[0]


@dataclass(frozen=True)
class SdkExecutionResult:
    value: dict[str, Any]
    stdout: str
    stderr: str
    source_sha256: str


def load_strategy_module(source: str) -> LoadedStrategy:
    inspection = inspect_strategy_source(source)
    _preflight_runtime(inspection.runtime_requirements)
    namespace: dict[str, Any] = {
        "__name__": "alphalab_strategy_sdk_v1",
        "__package__": None,
    }
    exec(compile(source, "<alphalab-strategy-sdk-v1>", "exec"), namespace)
    by_kind: dict[str, list[RegisteredCallable]] = {}
    by_id: dict[str, RegisteredCallable] = {}
    event_handlers: dict[Event, RegisteredCallable] = {}
    seen_functions: set[int] = set()
    for value in namespace.values():
        registration = getattr(value, "__alphalab_registration__", None)
        if not callable(value) or not isinstance(registration, Registration):
            continue
        if id(value) in seen_functions:
            continue
        seen_functions.add(id(value))
        item = RegisteredCallable(registration=registration, function=value)
        if registration.id in by_id:
            raise SdkRuntimeError(
                f"duplicate runtime registration id {registration.id!r}", phase="register"
            )
        by_id[registration.id] = item
        by_kind.setdefault(registration.kind, []).append(item)
        if registration.kind == "event":
            assert registration.event is not None
            if registration.event in event_handlers:
                raise SdkRuntimeError(
                    f"duplicate runtime handler for {registration.event.value}", phase="register"
                )
            event_handlers[registration.event] = item
    for kind in ("universe", "signal", "portfolio", "execution"):
        if len(by_kind.get(kind, [])) != 1:
            raise SdkRuntimeError(f"runtime registry requires exactly one {kind}", phase="register")
    return LoadedStrategy(
        source_sha256=inspection.source_sha256,
        inspection=inspection,
        by_kind=MappingProxyType({key: tuple(value) for key, value in by_kind.items()}),
        by_id=MappingProxyType(by_id),
        event_handlers=MappingProxyType(event_handlers),
    )


class SdkExecutionSession:
    """A strategy module loaded once in a child process for a bounded run."""

    def __init__(self, source: str, *, timeout_seconds: float = 15.0) -> None:
        self.inspection = inspect_strategy_source(source)
        self.source = source
        self.timeout_seconds = float(timeout_seconds)
        self._process: mp.Process | None = None
        self._connection: Connection | None = None

    def __enter__(self) -> "SdkExecutionSession":
        context = mp.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_session_worker,
            args=(child, self.source),
            daemon=True,
            name="alphalab-sdk-v1",
        )
        process.start()
        child.close()
        self._process = process
        self._connection = parent
        response = self._receive("strategy initialization")
        if not response.get("ok"):
            self.close(force=True)
            raise _error_from_response(response, self.inspection.source_sha256)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close(force=exc is not None)

    def close(self, *, force: bool = False) -> None:
        connection, process = self._connection, self._process
        self._connection = None
        self._process = None
        if connection is not None:
            if not force:
                try:
                    connection.send({"operation": "close"})
                except (BrokenPipeError, EOFError, OSError):
                    pass
            connection.close()
        if process is not None:
            process.join(timeout=1.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)
            if process.is_alive():
                process.kill()

    def execute(self, operation: str, payload: Mapping[str, Any]) -> SdkExecutionResult:
        if self._connection is None or self._process is None:
            raise RuntimeError("SDK execution session is not open")
        try:
            self._connection.send({"operation": operation, "payload": dict(payload)})
        except (BrokenPipeError, EOFError, OSError) as exc:
            raise SdkRuntimeError(
                "strategy worker is unavailable",
                phase="execute",
                source_sha256=self.inspection.source_sha256,
            ) from exc
        response = self._receive(operation)
        if not response.get("ok"):
            raise _error_from_response(response, self.inspection.source_sha256)
        value = response.get("value")
        if not isinstance(value, dict):
            raise SdkRuntimeError(
                "strategy worker returned an invalid result",
                phase="output",
                source_sha256=self.inspection.source_sha256,
            )
        return SdkExecutionResult(
            value=value,
            stdout=str(response.get("stdout") or ""),
            stderr=str(response.get("stderr") or ""),
            source_sha256=self.inspection.source_sha256,
        )

    def configure(self, payload: Mapping[str, Any]) -> None:
        """Load immutable run data into the worker once instead of per event."""

        result = self.execute("configure", payload)
        if not result.value.get("configured"):
            raise SdkRuntimeError(
                "strategy worker rejected run context",
                phase="input",
                source_sha256=self.inspection.source_sha256,
            )

    def _receive(self, operation: str) -> dict[str, Any]:
        assert self._connection is not None
        assert self._process is not None
        if not self._connection.poll(self.timeout_seconds):
            self.close(force=True)
            raise SdkRuntimeError(
                f"{operation} exceeded the {self.timeout_seconds:g}s timeout",
                phase="execute",
                source_sha256=self.inspection.source_sha256,
            )
        try:
            response = self._connection.recv()
        except (EOFError, OSError) as exc:
            exit_code = self._process.exitcode
            raise SdkRuntimeError(
                f"strategy worker exited unexpectedly (code={exit_code})",
                phase="execute",
                source_sha256=self.inspection.source_sha256,
            ) from exc
        if not isinstance(response, dict):
            raise SdkRuntimeError("strategy worker protocol error", phase="output")
        return response


def execute_sdk_operation(
    source: str,
    operation: str,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: float = 15.0,
) -> SdkExecutionResult:
    with SdkExecutionSession(source, timeout_seconds=timeout_seconds) as session:
        return session.execute(operation, payload)


def probe_sdk_operation(
    source: str,
    operation: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Run a bounded synthetic save probe in the current trusted process.

    Real evaluations and backtests use :class:`SdkExecutionSession`.  Save
    validation invokes this helper only after explicit save confirmation so it
    also works in CLI and test processes that cannot safely spawn from stdin.
    """

    return _dispatch(load_strategy_module(source), operation, payload)


def _session_worker(connection: Connection, source: str) -> None:
    startup_stdout = io.StringIO()
    startup_stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(startup_stdout), contextlib.redirect_stderr(startup_stderr):
            strategy = load_strategy_module(source)
        connection.send(
            {
                "ok": True,
                "stdout": _bounded(startup_stdout.getvalue()),
                "stderr": _bounded(startup_stderr.getvalue()),
            }
        )
    except Exception as exc:
        connection.send(_exception_response(exc, startup_stdout, startup_stderr))
        connection.close()
        return
    configured_payload: dict[str, Any] = {}
    while True:
        try:
            request = connection.recv()
        except EOFError:
            break
        if request.get("operation") == "close":
            break
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                operation = str(request.get("operation"))
                request_payload = dict(request.get("payload") or {})
                if operation == "configure":
                    configured_payload = request_payload
                    value = {"configured": True}
                else:
                    value = _dispatch(
                        strategy,
                        operation,
                        {**configured_payload, **request_payload},
                    )
            connection.send(
                {
                    "ok": True,
                    "value": value,
                    "stdout": _bounded(stdout.getvalue()),
                    "stderr": _bounded(stderr.getvalue()),
                }
            )
        except Exception as exc:
            connection.send(_exception_response(exc, stdout, stderr))
    connection.close()


def _dispatch(
    strategy: LoadedStrategy, operation: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    if operation == "factor":
        return _evaluate_factor(strategy, payload)
    if operation in {"signal", "portfolio", "execution", "event"}:
        return _evaluate_event(
            strategy, payload, stop_after=None if operation == "event" else operation
        )
    raise SdkRuntimeError(f"unsupported SDK operation: {operation}", phase="input")


def _evaluate_factor(strategy: LoadedStrategy, payload: Mapping[str, Any]) -> dict[str, Any]:
    factor_id = str(payload.get("factor_id") or "")
    state = State(payload.get("state") or {})
    contexts, active_symbols, cache = _build_contexts(strategy, payload)
    if factor_id not in strategy.by_id or strategy.by_id[factor_id].registration.kind != "factor":
        raise SdkRuntimeError(
            f"registered factor not found: {factor_id}", phase="input", entrypoint_id=factor_id
        )
    parameters = dict(payload.get("parameters") or {})
    series = _call_factor(strategy, factor_id, contexts[FactorContext], parameters, cache)
    return {
        "factor_id": factor_id,
        "as_of": str(contexts[FactorContext].as_of),
        "universe": list(active_symbols),
        "values": _series_records(series),
        "state": state.validated(),
        "state_sha256": _state_hash(state),
        "invoked": [strategy.one("universe").registration.id, factor_id],
    }


def _evaluate_event(
    strategy: LoadedStrategy,
    payload: Mapping[str, Any],
    *,
    stop_after: str | None,
) -> dict[str, Any]:
    event = Event(payload.get("event", Event.SESSION_CLOSE.value))
    working = State(payload.get("state") or {})
    contexts, active_symbols, _ = _build_contexts(strategy, payload)
    signal_item = strategy.one("signal")
    invoked = [strategy.one("universe").registration.id]
    due = bool(payload.get("force_signal", False)) or _signal_due(
        strategy, signal_item, contexts[SignalContext], event
    )
    signal_result: SignalResult | None = None
    decision = _decision_from_payload(payload.get("last_decision"))
    decision_changed = False

    if due:
        try:
            raw_signal = signal_item.function(
                contexts[SignalContext], working, **dict(payload.get("signal_parameters") or {})
            )
        except Exception as exc:
            raise _entrypoint_error(exc, signal_item, event, contexts[SignalContext]) from exc
        signal_result = _coerce_signal(raw_signal)
        _validate_signal(signal_result, active_symbols)
        working = State(state_after(working, signal_result.state))
        invoked.append(signal_item.registration.id)
        if stop_after == "signal":
            return _event_payload(
                contexts[SignalContext],
                active_symbols,
                working,
                invoked,
                signal_result=signal_result,
                decision=None,
                policy=None,
                due=True,
                decision_changed=False,
            )
        portfolio_item = strategy.one("portfolio")
        try:
            raw_decision = portfolio_item.function(
                contexts[PortfolioContext],
                signal_result,
                working,
                **dict(payload.get("portfolio_parameters") or {}),
            )
        except Exception as exc:
            raise _entrypoint_error(exc, portfolio_item, event, contexts[PortfolioContext]) from exc
        decision = _coerce_decision(raw_decision)
        _validate_decision(decision, active_symbols, payload.get("limits") or {})
        working = State(state_after(working, decision.state))
        invoked.append(portfolio_item.registration.id)
        decision_changed = True
        if stop_after == "portfolio":
            return _event_payload(
                contexts[PortfolioContext],
                active_symbols,
                working,
                invoked,
                signal_result=signal_result,
                decision=decision,
                policy=None,
                due=True,
                decision_changed=True,
            )

    handler = strategy.event_handlers.get(event)
    if handler is not None and stop_after not in {"signal", "portfolio"}:
        try:
            raw_override = handler.function(contexts[StrategyContext], working)
        except Exception as exc:
            raise _entrypoint_error(exc, handler, event, contexts[StrategyContext]) from exc
        invoked.append(handler.registration.id)
        if raw_override is not None:
            decision = _coerce_decision(raw_override)
            _validate_decision(decision, active_symbols, payload.get("limits") or {})
            working = State(state_after(working, decision.state))
            decision_changed = True

    decision_handler = strategy.event_handlers.get(Event.DECISION)
    if decision_changed and event is not Event.DECISION and decision_handler is not None:
        decision_contexts, _, _ = _build_contexts(
            strategy,
            {
                **dict(payload),
                "event": Event.DECISION.value,
                "last_decision": _decision_payload(decision),
                "force_signal": False,
            },
        )
        decision_context = decision_contexts[StrategyContext]
        try:
            raw_override = decision_handler.function(decision_context, working)
        except Exception as exc:
            raise _entrypoint_error(
                exc, decision_handler, Event.DECISION, decision_context
            ) from exc
        invoked.append(decision_handler.registration.id)
        if raw_override is not None:
            decision = _coerce_decision(raw_override)
            _validate_decision(decision, active_symbols, payload.get("limits") or {})
            working = State(state_after(working, decision.state))

    policy: ExecutionPolicy | None = None
    if decision is not None and stop_after not in {"signal", "portfolio"}:
        execution_item = strategy.one("execution")
        try:
            raw_policy = execution_item.function(
                contexts[ExecutionContext],
                decision,
                **dict(payload.get("execution_parameters") or {}),
            )
        except Exception as exc:
            raise _entrypoint_error(exc, execution_item, event, contexts[ExecutionContext]) from exc
        policy = _coerce_policy(raw_policy)
        unknown_fallbacks = sorted(set(policy.fallback_candidates) - set(active_symbols))
        if unknown_fallbacks:
            raise SdkRuntimeError(
                f"execution fallback candidates are outside the active universe: {unknown_fallbacks}",
                phase="output",
                entrypoint_id=execution_item.registration.id,
            )
        invoked.append(execution_item.registration.id)
    return _event_payload(
        contexts[StrategyContext],
        active_symbols,
        working,
        invoked,
        signal_result=signal_result,
        decision=decision,
        policy=policy,
        due=due,
        decision_changed=decision_changed,
    )


def _build_contexts(
    strategy: LoadedStrategy,
    payload: Mapping[str, Any],
) -> tuple[
    dict[type[StrategyContext], StrategyContext],
    tuple[str, ...],
    dict[tuple[str, tuple[tuple[str, Any], ...]], pd.Series],
]:
    available_symbols = tuple(
        str(value).strip().upper() for value in payload.get("available_symbols") or ()
    )
    common = {
        "event": payload.get("event", Event.SESSION_CLOSE.value),
        "as_of": payload["as_of"],
        "sessions": payload.get("sessions") or [payload["as_of"]],
        "symbols": available_symbols,
        "bars": payload.get("bars") if payload.get("bars") is not None else pd.DataFrame(),
        "instruments": (
            payload.get("instruments") if payload.get("instruments") is not None else pd.DataFrame()
        ),
        "fundamentals": (
            payload.get("fundamentals")
            if payload.get("fundamentals") is not None
            else pd.DataFrame()
        ),
        "portfolio": _portfolio_from_payload(payload.get("portfolio") or {}),
        "last_decision": _decision_from_payload(payload.get("last_decision")),
        "seed": int(payload.get("seed", 0)),
    }
    universe_context = UniverseContext(**common)
    universe_item = strategy.one("universe")
    try:
        raw_universe = universe_item.function(universe_context)
    except Exception as exc:
        raise _entrypoint_error(
            exc, universe_item, Event(common["event"]), universe_context
        ) from exc
    universe_result = _coerce_universe(raw_universe)
    unknown = sorted(set(universe_result.symbols) - set(available_symbols))
    if unknown:
        raise SdkRuntimeError(
            f"universe returned symbols outside the point-in-time instrument set: {unknown}",
            phase="output",
            entrypoint_id=universe_item.registration.id,
            event=str(common["event"]),
            as_of=str(common["as_of"]),
        )
    active_symbols = tuple(universe_result.symbols)
    cache: dict[tuple[str, tuple[tuple[str, Any], ...]], pd.Series] = {}
    contexts: dict[type[StrategyContext], StrategyContext] = {UniverseContext: universe_context}

    def resolver(factor_id: str, parameters: dict[str, Any]) -> pd.Series:
        return _call_factor(strategy, factor_id, contexts[FactorContext], parameters, cache)

    active_common = {**common, "symbols": active_symbols, "factor_resolver": resolver}
    factor_context = FactorContext(**active_common)
    contexts.update(
        {
            FactorContext: factor_context,
            SignalContext: SignalContext(**active_common),
            PortfolioContext: PortfolioContext(**active_common),
            ExecutionContext: ExecutionContext(**active_common),
            StrategyContext: StrategyContext(**active_common),
        }
    )
    return contexts, active_symbols, cache


def _call_factor(
    strategy: LoadedStrategy,
    factor_id: str,
    context: FactorContext,
    parameters: dict[str, Any],
    cache: dict[tuple[str, tuple[tuple[str, Any], ...]], pd.Series],
) -> pd.Series:
    item = strategy.by_id.get(factor_id)
    if item is None or item.registration.kind != "factor":
        raise SdkRuntimeError(
            f"registered factor not found: {factor_id}", phase="input", entrypoint_id=factor_id
        )
    try:
        key = (factor_id, tuple(sorted(parameters.items())))
        hash(key)
    except TypeError as exc:
        raise SdkRuntimeError(
            "factor parameters must be hashable literals", phase="input", entrypoint_id=factor_id
        ) from exc
    if key in cache:
        return cache[key].copy()
    active_stack = getattr(context, "_alphalab_factor_stack", ())
    if factor_id in active_stack:
        raise SdkRuntimeError(
            f"factor dependency cycle: {' -> '.join((*active_stack, factor_id))}",
            phase="execute",
            entrypoint_id=factor_id,
        )
    # Contexts are intentionally slot-based; cycle detection is carried by the
    # request-local closure rather than attached to user-visible context state.
    stack_key = ("__stack__", ())
    stack = cache.get(stack_key)  # type: ignore[arg-type]
    stack_values = list(stack.tolist()) if isinstance(stack, pd.Series) else []
    if factor_id in stack_values:
        raise SdkRuntimeError(
            f"factor dependency cycle: {' -> '.join([*stack_values, factor_id])}",
            phase="execute",
            entrypoint_id=factor_id,
        )
    cache[stack_key] = pd.Series([*stack_values, factor_id], dtype=object)  # type: ignore[index]
    try:
        raw = item.function(context, **parameters)
    except Exception as exc:
        raise _entrypoint_error(exc, item, context.event, context) from exc
    finally:
        cache[stack_key] = pd.Series(stack_values, dtype=object)  # type: ignore[index]
    if not isinstance(raw, pd.Series):
        raise SdkRuntimeError(
            f"factor {factor_id} must return pandas.Series",
            phase="output",
            entrypoint_id=factor_id,
            event=context.event.value,
            as_of=str(context.as_of),
        )
    series = raw.copy()
    series.index = series.index.map(lambda value: str(value).strip().upper())
    if series.index.has_duplicates:
        raise SdkRuntimeError(
            f"factor {factor_id} returned duplicate symbols",
            phase="output",
            entrypoint_id=factor_id,
        )
    unknown = sorted(set(series.index) - set(context.universe))
    if unknown:
        raise SdkRuntimeError(
            f"factor {factor_id} returned out-of-universe symbols: {unknown}",
            phase="output",
            entrypoint_id=factor_id,
        )
    series = pd.to_numeric(series, errors="coerce").reindex(context.universe)
    finite_or_nan = series.map(lambda value: pd.isna(value) or math.isfinite(float(value)))
    if not bool(finite_or_nan.all()):
        raise SdkRuntimeError(
            f"factor {factor_id} returned infinite values", phase="output", entrypoint_id=factor_id
        )
    cache[key] = series
    return series.copy()


def _signal_due(
    strategy: LoadedStrategy,
    item: RegisteredCallable,
    context: SignalContext,
    event: Event,
) -> bool:
    value = item.registration.metadata.get("schedule")
    if isinstance(value, Schedule):
        return schedule_is_due(value, context.calendar, context.as_of, event)
    if callable(value):
        registration = getattr(value, "__alphalab_registration__", None)
        if not isinstance(registration, Registration) or registration.kind != "schedule":
            raise SdkRuntimeError(
                "custom signal schedule must use @schedule",
                phase="register",
                entrypoint_id=item.registration.id,
            )
        return bool(value(context.calendar, context.as_of))
    raise SdkRuntimeError(
        "signal requires a Schedule or @schedule function",
        phase="register",
        entrypoint_id=item.registration.id,
    )


def _validate_signal(result: SignalResult, active_symbols: tuple[str, ...]) -> None:
    unknown = sorted(set(result.selected) - set(active_symbols))
    if unknown:
        raise SdkRuntimeError(f"signal selected out-of-universe symbols: {unknown}", phase="output")
    missing_scores = sorted(set(result.selected) - set(result.scores))
    if missing_scores:
        raise SdkRuntimeError(
            f"signal omitted scores for selected symbols: {missing_scores}", phase="output"
        )


def _validate_decision(
    decision: PortfolioDecision,
    active_symbols: tuple[str, ...],
    limits: Mapping[str, Any],
) -> None:
    unknown = sorted(set(decision.target_weights) - set(active_symbols))
    if unknown:
        raise SdkRuntimeError(
            f"portfolio returned out-of-universe symbols: {unknown}", phase="output"
        )
    max_weight = float(limits.get("max_weight", 1.0))
    gross_limit = float(limits.get("max_gross_exposure", 1.0))
    violations = sorted(
        symbol for symbol, weight in decision.target_weights.items() if weight > max_weight + 1e-9
    )
    if violations:
        raise SdkRuntimeError(f"target weights exceed max_weight for: {violations}", phase="output")
    gross = float(sum(decision.target_weights.values()))
    if gross > gross_limit + 1e-9:
        raise SdkRuntimeError(
            f"target gross exposure {gross:g} exceeds {gross_limit:g}", phase="output"
        )


def _coerce_universe(value: Any) -> UniverseResult:
    if isinstance(value, UniverseResult):
        return value
    if isinstance(value, dict):
        return UniverseResult(**value)
    raise SdkRuntimeError("@universe must return UniverseResult", phase="output")


def _coerce_signal(value: Any) -> SignalResult:
    if isinstance(value, SignalResult):
        return value
    if isinstance(value, dict):
        return SignalResult(**value)
    raise SdkRuntimeError("@signal must return SignalResult", phase="output")


def _coerce_decision(value: Any) -> PortfolioDecision:
    if isinstance(value, PortfolioDecision):
        return value
    if isinstance(value, dict):
        return PortfolioDecision(**value)
    raise SdkRuntimeError("strategy decision must be PortfolioDecision", phase="output")


def _coerce_policy(value: Any) -> ExecutionPolicy:
    if isinstance(value, ExecutionPolicy):
        return value
    if isinstance(value, dict):
        return ExecutionPolicy(**value)
    raise SdkRuntimeError("@execution must return ExecutionPolicy", phase="output")


def _decision_from_payload(value: Any) -> PortfolioDecision | None:
    if value is None or isinstance(value, PortfolioDecision):
        return value
    if isinstance(value, Mapping):
        return PortfolioDecision(
            target_weights=value.get("target_weights") or {},
            state=value.get("state"),
            reason=value.get("reason"),
            diagnostics=value.get("diagnostics"),
        )
    raise SdkRuntimeError("last_decision is invalid", phase="input")


def _portfolio_from_payload(value: Mapping[str, Any]) -> PortfolioSnapshot:
    positions = tuple(
        Holding(
            symbol=str(item["symbol"]).strip().upper(),
            weight=float(item.get("weight", 0.0)),
            close=float(item["close"]) if item.get("close") is not None else None,
            entry_price=float(item["entry_price"]) if item.get("entry_price") is not None else None,
            return_since_entry=float(item.get("return_since_entry", 0.0)),
        )
        for item in value.get("positions") or ()
    )
    return PortfolioSnapshot(
        positions=positions,
        cash_weight=float(value.get("cash_weight", 1.0)),
        equity=float(value.get("equity", 1.0)),
    )


def _event_payload(
    context: StrategyContext,
    active_symbols: tuple[str, ...],
    state: State,
    invoked: list[str],
    *,
    signal_result: SignalResult | None,
    decision: PortfolioDecision | None,
    policy: ExecutionPolicy | None,
    due: bool,
    decision_changed: bool,
) -> dict[str, Any]:
    return {
        "event": context.event.value,
        "as_of": str(context.as_of),
        "universe": list(active_symbols),
        "signal_due": due,
        "signal": _signal_payload(signal_result),
        "decision": _decision_payload(decision),
        "decision_changed": decision_changed,
        "execution_policy": asdict(policy) if policy is not None else None,
        "state": state.validated(),
        "state_sha256": _state_hash(state),
        "invoked": invoked,
    }


def _signal_payload(value: SignalResult | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "selected": list(value.selected),
        "scores": dict(value.scores),
        "diagnostics": value.diagnostics,
    }


def _decision_payload(value: PortfolioDecision | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "target_weights": dict(value.target_weights),
        "reason": value.reason,
        "diagnostics": value.diagnostics,
    }


def _series_records(series: pd.Series) -> list[dict[str, Any]]:
    return [
        {"symbol": str(symbol), "value": None if pd.isna(value) else float(value)}
        for symbol, value in series.items()
    ]


def _state_hash(state: Mapping[str, Any]) -> str:
    import json

    encoded = json.dumps(
        State(state).validated(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _preflight_runtime(requirements: Mapping[str, Any]) -> None:
    for package, raw_specifier in requirements.items():
        try:
            installed = importlib.metadata.version(str(package))
        except importlib.metadata.PackageNotFoundError as exc:
            raise SdkRuntimeError(
                f"required runtime package is unavailable: {package}", phase="input"
            ) from exc
        try:
            constraint = SpecifierSet(str(raw_specifier))
        except InvalidSpecifier as exc:
            raise SdkRuntimeError(
                f"invalid runtime requirement for {package}: {raw_specifier}", phase="register"
            ) from exc
        if installed not in constraint:
            raise SdkRuntimeError(
                f"runtime requirement not satisfied: {package}{raw_specifier} (installed {installed})",
                phase="input",
            )


def _entrypoint_error(
    exc: Exception,
    item: RegisteredCallable,
    event: Event,
    context: StrategyContext,
) -> SdkRuntimeError:
    if isinstance(exc, SdkRuntimeError):
        return exc
    return SdkRuntimeError(
        f"{type(exc).__name__}: {exc}",
        phase="execute",
        entrypoint_id=item.registration.id,
        event=event.value,
        as_of=str(context.as_of),
        traceback_text=_bounded(traceback.format_exc()),
    )


def _exception_response(exc: Exception, stdout: io.StringIO, stderr: io.StringIO) -> dict[str, Any]:
    if isinstance(exc, SdkRuntimeError):
        error = exc.to_dict()
    else:
        error = {
            "message": f"{type(exc).__name__}: {exc}",
            "phase": "execute",
            "entrypoint_id": None,
            "event": None,
            "as_of": None,
            "committed": False,
            "traceback": _bounded(traceback.format_exc()),
        }
    return {
        "ok": False,
        "error": error,
        "stdout": _bounded(stdout.getvalue()),
        "stderr": _bounded(stderr.getvalue()),
    }


def _error_from_response(response: Mapping[str, Any], source_sha256: str) -> SdkRuntimeError:
    error = response.get("error") if isinstance(response.get("error"), Mapping) else {}
    return SdkRuntimeError(
        str(error.get("message") or "unknown SDK runtime error"),
        phase=str(error.get("phase") or "execute"),
        entrypoint_id=error.get("entrypoint_id"),
        event=error.get("event"),
        as_of=error.get("as_of"),
        source_sha256=source_sha256,
        committed=bool(error.get("committed", False)),
        traceback_text=error.get("traceback"),
    )


def _bounded(value: str) -> str:
    return (
        value if len(value) <= MAX_LOG_CHARS else value[:MAX_LOG_CHARS] + "\n... output truncated"
    )


__all__ = [
    "LoadedStrategy",
    "SdkExecutionResult",
    "SdkExecutionSession",
    "SdkRuntimeError",
    "execute_sdk_operation",
    "load_strategy_module",
    "probe_sdk_operation",
]
