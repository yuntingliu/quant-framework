"""Public value objects and point-in-time contexts for Strategy SDK v1."""

from __future__ import annotations

import copy
import json
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

JsonObject = dict[str, Any]
MAX_STATE_BYTES = 64_000


class Event(str, Enum):
    SESSION_OPEN = "session_open"
    SESSION_CLOSE = "session_close"
    DECISION = "decision"
    FILL = "fill"
    REJECTION = "rejection"


@dataclass(frozen=True)
class Schedule:
    frequency: str
    selector: str
    at: str

    def __post_init__(self) -> None:
        if self.frequency not in {"daily", "weekly", "monthly"}:
            raise ValueError("unsupported schedule frequency")
        if self.selector not in {"every", "first_trading_day", "last_trading_day"}:
            raise ValueError("unsupported schedule selector")
        if self.at not in {"open", "close"}:
            raise ValueError("schedule at must be open or close")

    def to_dict(self) -> dict[str, str]:
        return {
            "frequency": self.frequency,
            "selector": self.selector,
            "at": self.at,
        }


class Daily:
    @staticmethod
    def at(at: str) -> Schedule:
        return Schedule("daily", "every", at)


class Weekly:
    @staticmethod
    def last_trading_day(*, at: str) -> Schedule:
        return Schedule("weekly", "last_trading_day", at)

    @staticmethod
    def first_trading_day(*, at: str) -> Schedule:
        return Schedule("weekly", "first_trading_day", at)


class Monthly:
    @staticmethod
    def last_trading_day(*, at: str) -> Schedule:
        return Schedule("monthly", "last_trading_day", at)

    @staticmethod
    def first_trading_day(*, at: str) -> Schedule:
        return Schedule("monthly", "first_trading_day", at)


@dataclass(frozen=True)
class Parameter:
    label: str | None = None
    description: str | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    step: float | int | None = None


def _symbols(values: Iterable[Any]) -> tuple[str, ...]:
    result = tuple(str(value).strip().upper() for value in values if str(value).strip())
    if len(result) != len(set(result)):
        raise ValueError("symbols must be unique")
    return result


def _json_copy(value: Any, *, label: str, max_bytes: int = MAX_STATE_BYTES) -> Any:
    def reject_non_finite(item: Any) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{label} contains a non-finite number")
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                raise ValueError(f"{label} keys must be strings")
            for child in item.values():
                reject_non_finite(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                reject_non_finite(child)
        elif item is not None and not isinstance(item, (bool, int, float, str)):
            raise ValueError(f"{label} contains unsupported type {type(item).__name__}")

    reject_non_finite(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} bytes")
    return json.loads(encoded)


class State(dict[str, Any]):
    """One event-local working copy of durable JSON strategy state."""

    def __init__(self, value: Mapping[str, Any] | None = None):
        super().__init__(_json_copy(dict(value or {}), label="state"))

    def validated(self) -> dict[str, Any]:
        return _json_copy(dict(self), label="state")


@dataclass(frozen=True)
class UniverseResult:
    symbols: Sequence[str]
    diagnostics: JsonObject | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", _symbols(self.symbols))
        if self.diagnostics is not None:
            object.__setattr__(
                self,
                "diagnostics",
                _json_copy(self.diagnostics, label="universe diagnostics"),
            )


@dataclass(frozen=True)
class SignalResult:
    selected: Sequence[str]
    scores: Mapping[str, float] | pd.Series
    state: JsonObject | None = None
    diagnostics: JsonObject | None = None

    def __post_init__(self) -> None:
        selected = _symbols(self.selected)
        raw_scores = (
            self.scores.to_dict() if isinstance(self.scores, pd.Series) else dict(self.scores)
        )
        scores: dict[str, float] = {}
        for raw_symbol, raw_score in raw_scores.items():
            symbol = str(raw_symbol).strip().upper()
            score = float(raw_score)
            if not math.isfinite(score):
                raise ValueError(f"signal score for {symbol} must be finite")
            scores[symbol] = score
        object.__setattr__(self, "selected", selected)
        object.__setattr__(self, "scores", MappingProxyType(scores))
        if self.state is not None:
            object.__setattr__(self, "state", _json_copy(self.state, label="signal state"))
        if self.diagnostics is not None:
            object.__setattr__(
                self,
                "diagnostics",
                _json_copy(self.diagnostics, label="signal diagnostics"),
            )


@dataclass(frozen=True)
class PortfolioDecision:
    target_weights: Mapping[str, float]
    state: JsonObject | None = None
    reason: str | None = None
    diagnostics: JsonObject | None = None

    def __post_init__(self) -> None:
        weights: dict[str, float] = {}
        for raw_symbol, raw_weight in dict(self.target_weights).items():
            symbol = str(raw_symbol).strip().upper()
            weight = float(raw_weight)
            if not math.isfinite(weight) or weight < 0:
                raise ValueError(f"target weight for {symbol} must be finite and non-negative")
            if weight > 1e-12:
                weights[symbol] = weight
        object.__setattr__(self, "target_weights", MappingProxyType(weights))
        if self.state is not None:
            object.__setattr__(self, "state", _json_copy(self.state, label="portfolio state"))
        if self.diagnostics is not None:
            object.__setattr__(
                self,
                "diagnostics",
                _json_copy(self.diagnostics, label="portfolio diagnostics"),
            )


@dataclass(frozen=True)
class ExecutionPolicy:
    activation: str = "next_session_open"
    commission_rate: float = 0.00025
    slippage_rate: float = 0.0002
    max_participation_rate: float = 0.1
    impact_rate: float = 0.0
    fallback_candidates: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.activation not in {"next_session_open", "next_session_close"}:
            raise ValueError("execution activation must be next_session_open or next_session_close")
        for name in (
            "commission_rate",
            "slippage_rate",
            "max_participation_rate",
            "impact_rate",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)
        if self.max_participation_rate <= 0 or self.max_participation_rate > 1:
            raise ValueError("max_participation_rate must be in (0, 1]")
        object.__setattr__(self, "fallback_candidates", _symbols(self.fallback_candidates))


@dataclass(frozen=True)
class Holding:
    symbol: str
    weight: float
    close: float | None = None
    entry_price: float | None = None
    return_since_entry: float = 0.0


@dataclass(frozen=True)
class PortfolioSnapshot:
    positions: tuple[Holding, ...] = ()
    cash_weight: float = 1.0
    equity: float = 1.0

    @property
    def primary_holding(self) -> Holding | None:
        return max(self.positions, key=lambda item: item.weight, default=None)

    def weight(self, symbol: str) -> float:
        normalized = str(symbol).strip().upper()
        return next((item.weight for item in self.positions if item.symbol == normalized), 0.0)


@dataclass(frozen=True)
class CalendarView:
    sessions: tuple[pd.Timestamp, ...]

    def _position(self, session: Any) -> int:
        value = pd.Timestamp(session).normalize()
        try:
            return self.sessions.index(value)
        except ValueError as exc:
            raise ValueError(f"{value.date()} is not a strategy session") from exc

    def is_month_end(self, session: Any) -> bool:
        index = self._position(session)
        return (
            index == len(self.sessions) - 1
            or self.sessions[index + 1].month != self.sessions[index].month
        )

    def is_month_start(self, session: Any) -> bool:
        index = self._position(session)
        return index == 0 or self.sessions[index - 1].month != self.sessions[index].month

    def is_week_end(self, session: Any) -> bool:
        index = self._position(session)
        return (
            index == len(self.sessions) - 1
            or self.sessions[index + 1].isocalendar().week
            != self.sessions[index].isocalendar().week
        )

    def is_week_start(self, session: Any) -> bool:
        index = self._position(session)
        return (
            index == 0
            or self.sessions[index - 1].isocalendar().week
            != self.sessions[index].isocalendar().week
        )


class StrategyContext:
    """Immutable capability view built from a bounded point-in-time snapshot."""

    __slots__ = (
        "_as_of",
        "_bars",
        "_event",
        "_factor_resolver",
        "_fundamentals",
        "_daily_factors",
        "_index_components",
        "_instruments",
        "_last_decision",
        "_portfolio",
        "_random",
        "_symbols",
        "calendar",
    )

    def __init__(
        self,
        *,
        event: Event | str,
        as_of: Any,
        sessions: Sequence[Any] | CalendarView,
        symbols: Sequence[str],
        bars: pd.DataFrame | Sequence[Mapping[str, Any]],
        instruments: pd.DataFrame | Sequence[Mapping[str, Any]] = (),
        fundamentals: pd.DataFrame | Sequence[Mapping[str, Any]] = (),
        daily_factors: pd.DataFrame | Sequence[Mapping[str, Any]] = (),
        index_components: pd.DataFrame | Sequence[Mapping[str, Any]] = (),
        portfolio: PortfolioSnapshot | None = None,
        last_decision: PortfolioDecision | None = None,
        seed: int = 0,
        factor_resolver: Callable[..., pd.Series] | None = None,
    ) -> None:
        as_of_value = pd.Timestamp(as_of)
        if as_of_value.tzinfo is not None:
            as_of_value = as_of_value.tz_localize(None)
        self._as_of = as_of_value
        self._event = Event(event)
        self.calendar = (
            sessions
            if isinstance(sessions, CalendarView)
            else CalendarView(tuple(pd.Timestamp(value).normalize() for value in sessions))
        )
        self._symbols = _symbols(symbols)
        # Static run data is shared by all event contexts.  Keep the immutable
        # normalized frames here and apply the as-of boundary only in accessors;
        # eagerly copying the complete history for every context and session
        # makes multi-year event backtests quadratic in practice.
        self._bars = _time_frame(bars, "date")
        self._instruments = _pit_frame(instruments, as_of_value, "snapshot_date", optional=True)
        if not self._instruments.empty and "snapshot_date" in self._instruments:
            latest_snapshot = self._instruments["snapshot_date"].max()
            self._instruments = self._instruments.loc[
                self._instruments["snapshot_date"].eq(latest_snapshot)
            ].reset_index(drop=True)
        for column in ("listed_date", "list_date"):
            if column in self._instruments:
                listed = pd.to_datetime(self._instruments[column], errors="coerce")
                self._instruments = self._instruments.loc[
                    listed.isna() | listed.le(as_of_value)
                ].reset_index(drop=True)
                break
        for column in ("de_listed_date", "delisted_date"):
            if column in self._instruments:
                delisted = pd.to_datetime(self._instruments[column], errors="coerce")
                self._instruments = self._instruments.loc[
                    delisted.isna() | delisted.gt(as_of_value)
                ].reset_index(drop=True)
                break
        self._fundamentals = _time_frame(fundamentals, "available_date", optional=True)
        self._daily_factors = _time_frame(daily_factors, "date", optional=True)
        self._index_components = _time_frame(index_components, "date", optional=True)
        self._portfolio = portfolio or PortfolioSnapshot()
        self._last_decision = last_decision
        self._random = random.Random(int(seed))
        self._factor_resolver = factor_resolver

    @classmethod
    def _derive(
        cls,
        source: "StrategyContext",
        *,
        symbols: Sequence[str],
        factor_resolver: Callable[..., pd.Series] | None,
    ) -> "StrategyContext":
        """Create another typed capability view over the same event snapshot."""

        derived = cls.__new__(cls)
        derived._as_of = source._as_of
        derived._bars = source._bars
        derived._event = source._event
        derived._factor_resolver = factor_resolver
        derived._fundamentals = source._fundamentals
        derived._daily_factors = source._daily_factors
        derived._index_components = source._index_components
        derived._instruments = source._instruments
        derived._last_decision = source._last_decision
        derived._portfolio = source._portfolio
        derived._random = random.Random()
        derived._random.setstate(source._random.getstate())
        derived._symbols = _symbols(symbols)
        derived.calendar = source.calendar
        return derived

    @property
    def event(self) -> Event:
        return self._event

    @property
    def as_of(self) -> pd.Timestamp:
        return self._as_of

    @property
    def universe(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def portfolio(self) -> PortfolioSnapshot:
        return self._portfolio

    @property
    def last_decision(self) -> PortfolioDecision | None:
        return self._last_decision

    @property
    def random(self) -> random.Random:
        return self._random

    def instruments(self, *, asset_type: str | None = None) -> pd.DataFrame:
        frame = self._instruments.copy()
        if asset_type is not None:
            if "asset_type" not in frame:
                return frame.iloc[0:0].copy()
            frame = frame.loc[frame["asset_type"].astype(str).str.upper().eq(asset_type.upper())]
        return frame.reset_index(drop=True)

    def current(self, field: str, *, symbols: Sequence[str] | None = None) -> pd.Series:
        requested = self._requested_symbols(symbols)
        if field not in self._bars:
            raise KeyError(f"market field is unavailable: {field}")
        rows = self._bars.loc[
            self._bars["date"].le(self._as_of) & self._bars["symbol"].isin(requested),
            list(dict.fromkeys(["date", "symbol", field])),
        ].sort_values("date")
        latest = rows.drop_duplicates("symbol", keep="last").set_index("symbol")
        return pd.to_numeric(latest[field], errors="coerce").reindex(requested).copy()

    def history(
        self,
        fields: str | Sequence[str],
        *,
        window: int,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        if int(window) < 1:
            raise ValueError("history window must be >= 1")
        requested = self._requested_symbols(symbols)
        names = [fields] if isinstance(fields, str) else list(fields)
        missing = [name for name in names if name not in self._bars]
        if missing:
            raise KeyError(f"market fields are unavailable: {missing}")
        rows = self._bars.loc[
            self._bars["date"].le(self._as_of) & self._bars["symbol"].isin(requested),
            list(dict.fromkeys(["date", "symbol", *names])),
        ].sort_values("date")

        def last_window(name: str) -> pd.DataFrame:
            # Match pivot_table's removal of all-NaN dates before taking the
            # window. Pivot only those sessions, rather than years of history.
            available = rows.loc[rows[name].notna(), ["date", "symbol", name]]
            dates = available["date"].drop_duplicates().tail(int(window))
            available = available.loc[available["date"].isin(dates)]
            return available.pivot_table(
                index="date", columns="symbol", values=name, aggfunc="last"
            ).reindex(columns=requested)

        if len(names) == 1:
            return last_window(names[0]).copy()
        pieces = {name: last_window(name) for name in names}
        return pd.concat(pieces, axis=1).copy()

    def fundamental(self, field: str, *, symbols: Sequence[str] | None = None) -> pd.Series:
        requested = self._requested_symbols(symbols)
        if field not in self._fundamentals:
            raise KeyError(f"fundamental field is unavailable: {field}")
        available = pd.Series(True, index=self._fundamentals.index)
        if "available_date" in self._fundamentals:
            available = self._fundamentals["available_date"].le(self._as_of)
        rows = self._fundamentals.loc[
            available & self._fundamentals["symbol"].isin(requested)
        ].copy()
        ordering = [name for name in ("available_date", "quarter") if name in rows]
        if ordering:
            rows = rows.sort_values(ordering)
        latest = rows.drop_duplicates("symbol", keep="last").set_index("symbol")
        return pd.to_numeric(latest[field], errors="coerce").reindex(requested).copy()

    def daily_factor(
        self,
        field: str,
        *,
        symbols: Sequence[str] | None = None,
    ) -> pd.Series:
        """Return the latest available value of one dated provider factor."""

        requested = self._requested_symbols(symbols)
        frame = self._daily_factors
        required = {"date", "symbol", "field", "value"}
        if frame.empty or not required.issubset(frame.columns):
            raise KeyError(f"daily factor is unavailable: {field}")
        rows = frame.loc[
            frame["date"].le(self._as_of)
            & frame["field"].astype(str).eq(str(field))
            & frame["symbol"].isin(requested)
        ].sort_values("date")
        if rows.empty:
            raise KeyError(f"daily factor is unavailable: {field}")
        latest = rows.drop_duplicates("symbol", keep="last").set_index("symbol")
        return pd.to_numeric(latest["value"], errors="coerce").reindex(requested).copy()

    def index_components(self, index_symbol: str) -> tuple[str, ...]:
        """Return the latest known membership snapshot at or before ``as_of``."""

        frame = self._index_components
        required = {"date", "index_symbol", "symbol"}
        if frame.empty or not required.issubset(frame.columns):
            raise KeyError(f"index components are unavailable: {index_symbol}")
        index_id = str(index_symbol).strip().upper()
        rows = frame.loc[
            frame["date"].le(self._as_of)
            & frame["index_symbol"].astype(str).str.upper().eq(index_id)
        ]
        if rows.empty:
            raise KeyError(f"index components are unavailable: {index_symbol}")
        latest = pd.to_datetime(rows["date"], errors="coerce").max()
        members = rows.loc[pd.to_datetime(rows["date"], errors="coerce").eq(latest), "symbol"]
        allowed = set(self._symbols)
        return tuple(
            sorted(
                symbol
                for symbol in members.dropna().astype(str).str.upper().unique()
                if symbol in allowed
            )
        )

    def factor(self, factor_id: str, **parameters: Any) -> pd.Series:
        if self._factor_resolver is None:
            raise RuntimeError("factor registry is unavailable in this context")
        return self._factor_resolver(str(factor_id), parameters).copy()

    def combine_factors(
        self,
        weights: Mapping[str, float],
        *,
        normalization: str = "rank",
        parameters: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> pd.Series:
        """Resolve, normalize, and combine registered factors on the active universe.

        Positive weights prefer larger factor values and negative weights prefer
        smaller values. The absolute weights are normalized to one so score scale
        stays stable when a factor is added or removed.
        """

        if normalization not in {"raw", "rank", "zscore"}:
            raise ValueError("factor normalization must be raw, rank, or zscore")
        raw_parameters = dict(parameters or {})
        unknown_parameters = sorted(set(raw_parameters) - set(weights))
        if unknown_parameters:
            raise ValueError(
                f"factor parameters reference unweighted factors: {unknown_parameters}"
            )

        resolved: list[pd.Series] = []
        total_weight = 0.0
        seen: set[str] = set()
        for raw_factor_id, raw_weight in dict(weights).items():
            factor_id = str(raw_factor_id).strip()
            if not factor_id or factor_id.startswith("_"):
                raise ValueError("factor weights require public factor ids")
            if factor_id in seen:
                raise ValueError(f"duplicate factor weight: {factor_id}")
            seen.add(factor_id)
            if isinstance(raw_weight, bool):
                raise ValueError(f"factor weight for {factor_id} must be numeric")
            weight = float(raw_weight)
            if not math.isfinite(weight):
                raise ValueError(f"factor weight for {factor_id} must be finite")
            if abs(weight) <= 1e-12:
                continue

            factor_parameters = raw_parameters.get(factor_id, {})
            if not isinstance(factor_parameters, Mapping):
                raise ValueError(f"parameters for factor {factor_id} must be a mapping")
            values = pd.to_numeric(
                self.factor(factor_id, **dict(factor_parameters)), errors="coerce"
            ).reindex(self.universe)
            if normalization == "rank":
                values = values.rank(method="average", pct=True)
            elif normalization == "zscore":
                deviation = float(values.std(ddof=0))
                values = (
                    (values - float(values.mean())) / deviation
                    if math.isfinite(deviation) and deviation > 1e-12
                    else values.where(values.isna(), 0.0)
                )
            resolved.append(values * weight)
            total_weight += abs(weight)

        if not resolved or total_weight <= 1e-12:
            raise ValueError("factor weights must contain at least one non-zero value")
        combined = pd.concat(resolved, axis=1).sum(axis=1, min_count=len(resolved))
        return (combined / total_weight).reindex(self.universe).copy()

    def _requested_symbols(self, symbols: Sequence[str] | None) -> list[str]:
        requested = list(_symbols(symbols or self._symbols))
        unknown = sorted(set(requested) - set(self._symbols))
        if unknown:
            raise ValueError(f"symbols are outside the active universe: {unknown}")
        return requested


class UniverseContext(StrategyContext):
    pass


class FactorContext(StrategyContext):
    pass


class SignalContext(StrategyContext):
    pass


class PortfolioContext(StrategyContext):
    pass


class ExecutionContext(StrategyContext):
    pass


def _pit_frame(
    value: pd.DataFrame | Sequence[Mapping[str, Any]],
    as_of: pd.Timestamp,
    date_field: str,
    *,
    optional: bool = False,
) -> pd.DataFrame:
    frame = _time_frame(value, date_field, optional=optional)
    if frame.empty:
        return frame
    frame = frame.loc[frame[date_field].notna() & frame[date_field].le(as_of)].copy()
    return frame.reset_index(drop=True)


def _time_frame(
    value: pd.DataFrame | Sequence[Mapping[str, Any]],
    date_field: str,
    *,
    optional: bool = False,
) -> pd.DataFrame:
    frame = value if isinstance(value, pd.DataFrame) else pd.DataFrame(list(value))
    if frame.empty:
        return frame
    normalized_fields = set(frame.attrs.get("_alphalab_time_fields") or ())
    if date_field in normalized_fields:
        return frame
    if date_field not in frame:
        if optional:
            return frame
        raise KeyError(f"point-in-time data requires {date_field}")
    copied = False
    if "symbol" in frame:
        symbols = frame["symbol"].astype(str)
        normalized = symbols.str.upper()
        if not symbols.equals(normalized):
            frame = frame.copy()
            copied = True
            frame["symbol"] = normalized
    if not pd.api.types.is_datetime64_any_dtype(frame[date_field]):
        if not copied:
            frame = frame.copy()
        frame[date_field] = pd.to_datetime(frame[date_field], errors="coerce")
    normalized_fields.add(date_field)
    # The worker owns these frames after configuration.  The marker lets all
    # event-specific contexts reuse them without rescanning every symbol and
    # date column on every trading day.
    frame.attrs["_alphalab_time_fields"] = tuple(sorted(normalized_fields))
    return frame


def schedule_is_due(
    value: Schedule,
    calendar: CalendarView,
    session: Any,
    event: Event,
) -> bool:
    if event not in {Event.SESSION_OPEN, Event.SESSION_CLOSE}:
        return False
    if value.at != ("open" if event is Event.SESSION_OPEN else "close"):
        return False
    if value.frequency == "daily":
        return True
    if value.frequency == "weekly":
        return (
            calendar.is_week_start(session)
            if value.selector == "first_trading_day"
            else calendar.is_week_end(session)
        )
    return (
        calendar.is_month_start(session)
        if value.selector == "first_trading_day"
        else calendar.is_month_end(session)
    )


def state_after(working: State, replacement: Mapping[str, Any] | None) -> dict[str, Any]:
    return State(replacement if replacement is not None else working).validated()


def clone_json(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return copy.deepcopy(State(value).validated())


__all__ = [
    "CalendarView",
    "Daily",
    "Event",
    "ExecutionContext",
    "ExecutionPolicy",
    "FactorContext",
    "Holding",
    "MAX_STATE_BYTES",
    "Monthly",
    "Parameter",
    "PortfolioContext",
    "PortfolioDecision",
    "PortfolioSnapshot",
    "Schedule",
    "SignalContext",
    "SignalResult",
    "State",
    "StrategyContext",
    "UniverseContext",
    "UniverseResult",
    "Weekly",
]
