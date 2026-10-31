"""Portfolio constraint objects and input validation for Strategy SDK v1.

The normative semantics are section 14 of ``docs/02_STRATEGY_SDK_V1_CONTRACT.md``.
Constraints are plain data from a closed set of kinds; this module never evaluates
portfolio weights against them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Iterable, Mapping, Sequence, Union

SCHEMA_VERSION = 1
DEFAULT_TOLERANCE = 1e-6
MAX_TOLERANCE = 1e-4
BENCHMARK_SUM_TOLERANCE = 1e-3
UNCLASSIFIED_GROUP = "__unclassified__"

MISSING_POLICIES = frozenset({"reject", "unclassified_group"})
RISK_ESTIMATORS = frozenset({"sample"})
CHECK_LAYERS = frozenset({"project", "strategy"})
CHECK_STAGES = frozenset({"optimizer", "target", "planned"})
CHECK_STATUSES = frozenset({"pass", "fail", "improved", "not_evaluated"})

ERROR_CODES = frozenset(
    {
        "CONSTRAINT_SPEC_INVALID",
        "CONSTRAINT_DATA_MISSING",
        "CONSTRAINT_DATA_FUTURE",
        "CONSTRAINT_DATA_STALE",
        "CONSTRAINT_DATA_DUPLICATE",
        "CONSTRAINT_DATA_INVALID",
        "CONSTRAINT_DATA_VERSION_INVALID",
        "UNCLASSIFIED_SECURITY",
    }
)

# "<source>:<level>", for example "citics_2019:1".
_CLASSIFICATION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*:[1-9]$")


class PortfolioConstraintError(ValueError):
    """Raised for an invalid constraint specification or constraint input data."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        constraint_id: str | None = None,
        subject: str | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown portfolio constraint error code: {code}")
        super().__init__(f"{code}: {message}")
        self.code = code
        self.constraint_id = constraint_id
        self.subject = subject


def _spec_error(message: str, constraint_id: str | None = None) -> PortfolioConstraintError:
    return PortfolioConstraintError("CONSTRAINT_SPEC_INVALID", message, constraint_id=constraint_id)


def canonical_json(value: Any) -> str:
    """Serialize JSON with sorted keys, no insignificant whitespace, and repr floats."""

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _symbol(value: Any) -> str:
    symbol = str(value).strip().upper()
    if not symbol:
        raise ValueError("symbol must be non-empty")
    return symbol


def _public_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip().startswith("_"):
        raise _spec_error(f"{label} must be a public identifier")
    return value.strip()


def _number(value: Any, label: str, constraint_id: str | None) -> float:
    # bool is an int subclass; True must not silently mean a 100% limit.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _spec_error(f"{label} must be a number", constraint_id)
    number = float(value)
    if not math.isfinite(number):
        raise _spec_error(f"{label} must be finite", constraint_id)
    return number


def _fraction(
    value: Any,
    label: str,
    constraint_id: str | None,
    *,
    positive: bool = False,
) -> float:
    number = _number(value, label, constraint_id)
    if number < 0 or number > 1 or (positive and number == 0):
        interval = "(0, 1]" if positive else "[0, 1]"
        raise _spec_error(f"{label} must be in {interval}", constraint_id)
    return number


def _optional_fraction(value: Any, label: str, constraint_id: str) -> float | None:
    return None if value is None else _fraction(value, label, constraint_id)


def _integer(value: Any, label: str, constraint_id: str | None, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _spec_error(f"{label} must be an integer >= {minimum}", constraint_id)
    return int(value)


def _classification(value: Any, constraint_id: str, *, optional: bool) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _CLASSIFICATION_PATTERN.match(value):
        raise _spec_error(
            "classification must look like '<source>:<level>', for example 'citics_2019:1'",
            constraint_id,
        )
    return value


def _groups(
    value: Any,
    constraint_id: str,
    *,
    securities: bool,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise _spec_error("groups must be a sequence of strings or None", constraint_id)
    groups: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _spec_error("groups must contain non-empty strings", constraint_id)
        # Without a classification, subjects are securities and follow symbol casing.
        groups.append(item.strip().upper() if securities else item.strip())
    if not groups:
        raise _spec_error("groups must not be empty; use None for every group", constraint_id)
    if len(groups) != len(set(groups)):
        raise _spec_error("groups must not repeat", constraint_id)
    return tuple(groups)


def _missing_policy(value: Any, constraint_id: str) -> str:
    if value not in MISSING_POLICIES:
        raise _spec_error(
            f"missing_policy must be one of {sorted(MISSING_POLICIES)}", constraint_id
        )
    return str(value)


def _reject_unknown_keys(payload: Mapping[str, Any], allowed: Iterable[str], label: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise _spec_error(f"{label} has unknown fields: {unknown}")


@dataclass(frozen=True, kw_only=True)
class RiskModelSpec:
    """Covariance inputs that let the core rebuild a risk-budget calculation."""

    lookback: int
    minimum_observations: int
    estimator: str = "sample"

    def __post_init__(self) -> None:
        lookback = _integer(self.lookback, "risk_model.lookback", None, minimum=2)
        minimum = _integer(
            self.minimum_observations, "risk_model.minimum_observations", None, minimum=2
        )
        if minimum > lookback:
            raise _spec_error("risk_model.minimum_observations must not exceed lookback")
        if self.estimator not in RISK_ESTIMATORS:
            raise _spec_error(f"risk_model.estimator must be one of {sorted(RISK_ESTIMATORS)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "lookback": self.lookback,
            "minimum_observations": self.minimum_observations,
            "estimator": self.estimator,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RiskModelSpec:
        _reject_unknown_keys(
            payload, ("lookback", "minimum_observations", "estimator"), "risk_model"
        )
        try:
            return cls(**dict(payload))
        except TypeError as exc:
            raise _spec_error(f"risk_model is incomplete: {exc}") from exc


@dataclass(frozen=True, kw_only=True)
class GroupWeightConstraint:
    """Bound the total weight of the securities in each selected group."""

    kind: ClassVar[str] = "group_weight"

    id: str
    classification: str
    groups: Sequence[str] | None = None
    lower: float | None = None
    upper: float | None = None
    missing_policy: str = "reject"

    def __post_init__(self) -> None:
        constraint_id = _public_id(self.id, "constraint id")
        object.__setattr__(self, "id", constraint_id)
        object.__setattr__(
            self,
            "classification",
            _classification(self.classification, constraint_id, optional=False),
        )
        object.__setattr__(self, "groups", _groups(self.groups, constraint_id, securities=False))
        lower = _optional_fraction(self.lower, "lower", constraint_id)
        upper = _optional_fraction(self.upper, "upper", constraint_id)
        if lower is None and upper is None:
            raise _spec_error("group_weight requires lower, upper, or both", constraint_id)
        if lower is not None and upper is not None and lower > upper:
            raise _spec_error("lower must not exceed upper", constraint_id)
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)
        object.__setattr__(
            self, "missing_policy", _missing_policy(self.missing_policy, constraint_id)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "classification": self.classification,
            "groups": None if self.groups is None else list(self.groups),
            "lower": self.lower,
            "upper": self.upper,
            "missing_policy": self.missing_policy,
        }


@dataclass(frozen=True, kw_only=True)
class BenchmarkDeviationConstraint:
    """Bound |portfolio weight - benchmark weight| per group, or per security."""

    kind: ClassVar[str] = "benchmark_deviation"

    id: str
    benchmark: str
    classification: str | None
    max_abs_deviation: float
    max_staleness_days: int
    groups: Sequence[str] | None = None
    missing_policy: str = "reject"

    def __post_init__(self) -> None:
        constraint_id = _public_id(self.id, "constraint id")
        object.__setattr__(self, "id", constraint_id)
        if not isinstance(self.benchmark, str) or not self.benchmark.strip():
            raise _spec_error("benchmark must be an index symbol", constraint_id)
        object.__setattr__(self, "benchmark", _symbol(self.benchmark))
        classification = _classification(self.classification, constraint_id, optional=True)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(
            self, "groups", _groups(self.groups, constraint_id, securities=classification is None)
        )
        object.__setattr__(
            self,
            "max_abs_deviation",
            _fraction(self.max_abs_deviation, "max_abs_deviation", constraint_id, positive=True),
        )
        object.__setattr__(
            self,
            "max_staleness_days",
            _integer(self.max_staleness_days, "max_staleness_days", constraint_id, minimum=0),
        )
        object.__setattr__(
            self, "missing_policy", _missing_policy(self.missing_policy, constraint_id)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "benchmark": self.benchmark,
            "classification": self.classification,
            "groups": None if self.groups is None else list(self.groups),
            "max_abs_deviation": self.max_abs_deviation,
            "max_staleness_days": self.max_staleness_days,
            "missing_policy": self.missing_policy,
        }


@dataclass(frozen=True, kw_only=True)
class RiskBudgetConstraint:
    """Cap the share of portfolio variance contributed by a group or security."""

    kind: ClassVar[str] = "risk_budget"

    id: str
    classification: str | None
    upper: float
    risk_model: RiskModelSpec
    groups: Sequence[str] | None = None
    missing_policy: str = "reject"

    def __post_init__(self) -> None:
        constraint_id = _public_id(self.id, "constraint id")
        object.__setattr__(self, "id", constraint_id)
        classification = _classification(self.classification, constraint_id, optional=True)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(
            self, "groups", _groups(self.groups, constraint_id, securities=classification is None)
        )
        object.__setattr__(self, "upper", _fraction(self.upper, "upper", constraint_id))
        if not isinstance(self.risk_model, RiskModelSpec):
            raise _spec_error("risk_model must be a RiskModelSpec", constraint_id)
        object.__setattr__(
            self, "missing_policy", _missing_policy(self.missing_policy, constraint_id)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "classification": self.classification,
            "groups": None if self.groups is None else list(self.groups),
            "upper": self.upper,
            "risk_model": self.risk_model.to_dict(),
            "missing_policy": self.missing_policy,
        }


PortfolioConstraint = Union[
    GroupWeightConstraint, BenchmarkDeviationConstraint, RiskBudgetConstraint
]
_CONSTRAINT_TYPES: dict[str, type] = {
    GroupWeightConstraint.kind: GroupWeightConstraint,
    BenchmarkDeviationConstraint.kind: BenchmarkDeviationConstraint,
    RiskBudgetConstraint.kind: RiskBudgetConstraint,
}
CONSTRAINT_KINDS = frozenset(_CONSTRAINT_TYPES)


def _constraint_from_dict(payload: Any) -> PortfolioConstraint:
    if not isinstance(payload, Mapping):
        raise _spec_error("each constraint must be an object")
    kind = payload.get("kind")
    constraint_type = _CONSTRAINT_TYPES.get(kind) if isinstance(kind, str) else None
    if constraint_type is None:
        raise _spec_error(f"constraint kind must be one of {sorted(CONSTRAINT_KINDS)}")
    fields = {key: value for key, value in payload.items() if key != "kind"}
    allowed = set(constraint_type.__dataclass_fields__) - {"kind"}
    _reject_unknown_keys(fields, allowed, f"{kind} constraint")
    if constraint_type is RiskBudgetConstraint and isinstance(fields.get("risk_model"), Mapping):
        fields["risk_model"] = RiskModelSpec.from_dict(fields["risk_model"])
    try:
        return constraint_type(**fields)
    except TypeError as exc:
        raise _spec_error(f"{kind} constraint is incomplete: {exc}") from exc


@dataclass(frozen=True, kw_only=True)
class PortfolioConstraintSet:
    """A date-independent rule set; one layer of hard portfolio constraints."""

    constraints: Sequence[PortfolioConstraint] = ()
    schema_version: int = SCHEMA_VERSION
    tolerance: float = DEFAULT_TOLERANCE

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != SCHEMA_VERSION:
            raise _spec_error(f"schema_version must be {SCHEMA_VERSION}")
        tolerance = _number(self.tolerance, "tolerance", None)
        if tolerance <= 0 or tolerance > MAX_TOLERANCE:
            raise _spec_error(f"tolerance must be in (0, {MAX_TOLERANCE:g}]")
        object.__setattr__(self, "tolerance", tolerance)
        constraints = tuple(self.constraints)
        seen: set[str] = set()
        for constraint in constraints:
            if not isinstance(constraint, tuple(_CONSTRAINT_TYPES.values())):
                raise _spec_error("constraints must be portfolio constraint objects")
            if constraint.id in seen:
                raise _spec_error(f"duplicate constraint id {constraint.id!r}", constraint.id)
            seen.add(constraint.id)
        object.__setattr__(self, "constraints", constraints)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tolerance": self.tolerance,
            "constraints": [constraint.to_dict() for constraint in self.constraints],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PortfolioConstraintSet:
        if not isinstance(payload, Mapping):
            raise _spec_error("constraint set must be an object")
        _reject_unknown_keys(
            payload, ("schema_version", "tolerance", "constraints"), "constraint set"
        )
        raw_constraints = payload.get("constraints", ())
        if isinstance(raw_constraints, (str, Mapping)) or not isinstance(raw_constraints, Iterable):
            raise _spec_error("constraints must be a list")
        return cls(
            constraints=tuple(_constraint_from_dict(item) for item in raw_constraints),
            schema_version=payload.get("schema_version", SCHEMA_VERSION),
            tolerance=payload.get("tolerance", DEFAULT_TOLERANCE),
        )

    @property
    def constraint_set_sha256(self) -> str:
        return _sha256(self.to_dict())

    @property
    def classifications(self) -> tuple[str, ...]:
        """Classification identifiers the data adapter must load."""

        return tuple(
            sorted({c.classification for c in self.constraints if c.classification is not None})
        )

    @property
    def benchmarks(self) -> tuple[str, ...]:
        """Benchmark symbols the data adapter must load."""

        return tuple(
            sorted(
                {
                    c.benchmark
                    for c in self.constraints
                    if isinstance(c, BenchmarkDeviationConstraint)
                }
            )
        )


def _date(value: Any, label: str) -> dt.date:
    # datetime and pandas.Timestamp are date subclasses; keep only the calendar day.
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            pass
    raise PortfolioConstraintError("CONSTRAINT_DATA_INVALID", f"{label} must be an ISO date")


def _source_version(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_INVALID", f"{label} source_version must be non-empty"
        )
    return value.strip()


def _unique_symbol_items(items: Any, label: str) -> list[tuple[str, Any]]:
    """Normalize symbols in a mapping or (symbol, value) pairs and reject duplicates."""

    pairs = items.items() if isinstance(items, Mapping) else items
    result: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for pair in pairs:
        try:
            raw_symbol, value = pair
            symbol = _symbol(raw_symbol)
        except (TypeError, ValueError) as exc:
            raise PortfolioConstraintError(
                "CONSTRAINT_DATA_INVALID", f"{label} contains a malformed symbol entry"
            ) from exc
        if symbol in seen:
            raise PortfolioConstraintError(
                "CONSTRAINT_DATA_DUPLICATE",
                f"{label} lists {symbol} more than once",
                subject=symbol,
            )
        seen.add(symbol)
        result.append((symbol, value))
    return result


@dataclass(frozen=True, kw_only=True)
class ClassificationSnapshot:
    """Point-in-time symbol -> group mapping for one classification scheme."""

    classification: str
    effective_date: dt.date
    groups: Mapping[str, str]
    source_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.classification, str) or not _CLASSIFICATION_PATTERN.match(
            self.classification
        ):
            raise PortfolioConstraintError(
                "CONSTRAINT_DATA_INVALID", "classification identifier is malformed"
            )
        label = f"classification {self.classification}"
        object.__setattr__(self, "effective_date", _date(self.effective_date, label))
        groups: dict[str, str] = {}
        for symbol, group in _unique_symbol_items(self.groups, label):
            if not isinstance(group, str) or not group.strip():
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_INVALID", f"{label} has no group for {symbol}", subject=symbol
                )
            if group.strip() == UNCLASSIFIED_GROUP:
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_INVALID",
                    f"{label} must not assign the reserved group {UNCLASSIFIED_GROUP}",
                    subject=symbol,
                )
            groups[symbol] = group.strip()
        object.__setattr__(self, "groups", MappingProxyType(groups))
        object.__setattr__(self, "source_version", _source_version(self.source_version, label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "effective_date": self.effective_date.isoformat(),
            "groups": dict(sorted(self.groups.items())),
            "source_version": self.source_version,
        }


@dataclass(frozen=True, kw_only=True)
class BenchmarkSnapshot:
    """Point-in-time benchmark weights expressed as fractions."""

    benchmark: str
    effective_date: dt.date
    weights: Mapping[str, float]
    source_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.benchmark, str) or not self.benchmark.strip():
            raise PortfolioConstraintError("CONSTRAINT_DATA_INVALID", "benchmark must be a symbol")
        object.__setattr__(self, "benchmark", _symbol(self.benchmark))
        label = f"benchmark {self.benchmark}"
        object.__setattr__(self, "effective_date", _date(self.effective_date, label))
        weights: dict[str, float] = {}
        for symbol, raw_weight in _unique_symbol_items(self.weights, label):
            if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_INVALID",
                    f"{label} weight for {symbol} is not a number",
                    subject=symbol,
                )
            weight = float(raw_weight)
            if not math.isfinite(weight) or weight < 0:
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_INVALID",
                    f"{label} weight for {symbol} must be finite and non-negative",
                    subject=symbol,
                )
            weights[symbol] = weight
        total = math.fsum(weights.values())
        # Percent-to-fraction conversion belongs to the adapter; never renormalize here.
        if abs(total - 1.0) > BENCHMARK_SUM_TOLERANCE:
            raise PortfolioConstraintError(
                "CONSTRAINT_DATA_INVALID",
                f"{label} weights sum to {total:.6g}, outside 1 +/- {BENCHMARK_SUM_TOLERANCE:g}",
            )
        object.__setattr__(self, "weights", MappingProxyType(weights))
        object.__setattr__(self, "source_version", _source_version(self.source_version, label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark": self.benchmark,
            "effective_date": self.effective_date.isoformat(),
            "weights": dict(sorted(self.weights.items())),
            "source_version": self.source_version,
        }


@dataclass(frozen=True, kw_only=True)
class PortfolioConstraintData:
    """Standardized classification and benchmark inputs for one decision date.

    ``data_version`` is computed from the snapshots when omitted.  A supplied
    value, for example from a frozen Run, must match the snapshot contents.
    """

    as_of: dt.date
    classifications: Mapping[str, ClassificationSnapshot]
    benchmarks: Mapping[str, BenchmarkSnapshot]
    data_version: str | None = None

    def __post_init__(self) -> None:
        as_of = _date(self.as_of, "as_of")
        object.__setattr__(self, "as_of", as_of)
        classifications = self._keyed(
            self.classifications, ClassificationSnapshot, "classification", as_of
        )
        benchmarks = self._keyed(self.benchmarks, BenchmarkSnapshot, "benchmark", as_of)
        object.__setattr__(self, "classifications", MappingProxyType(classifications))
        object.__setattr__(self, "benchmarks", MappingProxyType(benchmarks))
        computed = _sha256(self._content())
        if self.data_version is not None and self.data_version != computed:
            raise PortfolioConstraintError(
                "CONSTRAINT_DATA_VERSION_INVALID",
                "data_version does not match the snapshot contents",
            )
        object.__setattr__(self, "data_version", computed)

    @staticmethod
    def _keyed(
        snapshots: Mapping[str, Any],
        snapshot_type: type,
        key_field: str,
        as_of: dt.date,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, snapshot in dict(snapshots).items():
            if not isinstance(snapshot, snapshot_type):
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_INVALID",
                    f"{key_field} {key} is not a {snapshot_type.__name__}",
                )
            if getattr(snapshot, key_field) != key:
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_INVALID",
                    f"{key_field} key {key!r} does not match its snapshot",
                )
            if snapshot.effective_date > as_of:
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_FUTURE",
                    f"{key_field} {key} is effective {snapshot.effective_date.isoformat()}, "
                    f"after as_of {as_of.isoformat()}",
                )
            result[key] = snapshot
        return result

    def _content(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "classifications": {
                key: value.to_dict() for key, value in sorted(self.classifications.items())
            },
            "benchmarks": {key: value.to_dict() for key, value in sorted(self.benchmarks.items())},
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content(), "data_version": self.data_version}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PortfolioConstraintData:
        if not isinstance(payload, Mapping):
            raise PortfolioConstraintError(
                "CONSTRAINT_DATA_INVALID", "constraint data must be an object"
            )
        try:
            return cls(
                as_of=payload["as_of"],
                classifications={
                    key: ClassificationSnapshot(**value)
                    for key, value in dict(payload.get("classifications") or {}).items()
                },
                benchmarks={
                    key: BenchmarkSnapshot(**value)
                    for key, value in dict(payload.get("benchmarks") or {}).items()
                },
                data_version=payload.get("data_version"),
            )
        except (KeyError, TypeError) as exc:
            raise PortfolioConstraintError(
                "CONSTRAINT_DATA_INVALID", f"constraint data is malformed: {exc}"
            ) from exc

    def group_map(
        self,
        classification: str,
        symbols: Iterable[str],
        *,
        missing_policy: str = "reject",
        constraint_id: str | None = None,
    ) -> dict[str, str]:
        """Resolve each symbol's group, applying the constraint's missing policy."""

        snapshot = self.classifications.get(classification)
        if snapshot is None:
            raise PortfolioConstraintError(
                "CONSTRAINT_DATA_MISSING",
                f"classification {classification} is not loaded",
                constraint_id=constraint_id,
            )
        result: dict[str, str] = {}
        for raw_symbol in symbols:
            symbol = _symbol(raw_symbol)
            group = snapshot.groups.get(symbol)
            if group is None:
                if missing_policy != "unclassified_group":
                    raise PortfolioConstraintError(
                        "UNCLASSIFIED_SECURITY",
                        f"{symbol} has no group in {classification}",
                        constraint_id=constraint_id,
                        subject=symbol,
                    )
                group = UNCLASSIFIED_GROUP
            result[symbol] = group
        return result


def validate_constraint_data(
    constraint_set: PortfolioConstraintSet,
    data: PortfolioConstraintData,
    symbols: Iterable[str],
) -> None:
    """Check that ``data`` can evaluate every constraint for ``symbols``.

    ``symbols`` are the securities with non-zero weight in the vector to be
    checked, including holdings that are leaving the portfolio.  Benchmark
    members are added for grouped deviation constraints because their benchmark
    weight also needs a group.  The first problem raises ``PortfolioConstraintError``.
    """

    held = sorted({_symbol(symbol) for symbol in symbols})
    for constraint in constraint_set.constraints:
        members = list(held)
        if isinstance(constraint, BenchmarkDeviationConstraint):
            snapshot = data.benchmarks.get(constraint.benchmark)
            if snapshot is None:
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_MISSING",
                    f"benchmark {constraint.benchmark} is not loaded",
                    constraint_id=constraint.id,
                )
            age = (data.as_of - snapshot.effective_date).days
            if age > constraint.max_staleness_days:
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_STALE",
                    f"benchmark {constraint.benchmark} weights are {age} days old; "
                    f"limit is {constraint.max_staleness_days}",
                    constraint_id=constraint.id,
                )
            members.extend(symbol for symbol, weight in snapshot.weights.items() if weight > 0)
        if constraint.classification is not None:
            data.group_map(
                constraint.classification,
                sorted(set(members)),
                missing_policy=constraint.missing_policy,
                constraint_id=constraint.id,
            )


def bound_slack(actual: float, lower: float | None, upper: float | None) -> float:
    """Distance from ``actual`` to its nearest bound; negative when outside."""

    distances = []
    if lower is not None:
        distances.append(actual - lower)
    if upper is not None:
        distances.append(upper - actual)
    if not distances:
        raise ValueError("a bound check needs lower, upper, or both")
    return min(distances)


def _optional_finite(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"constraint check {label} must be finite or None")
    return float(value)


@dataclass(frozen=True, kw_only=True)
class ConstraintCheck:
    """One evaluated constraint for one subject at one stage."""

    layer: str
    constraint_id: str
    kind: str
    stage: str
    subject: str
    actual: float | None
    lower: float | None
    upper: float | None
    tolerance: float
    slack: float | None
    status: str
    constraint_set_sha256: str
    data_version: str | None = None
    reference: float | None = None
    reason_code: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.layer not in CHECK_LAYERS:
            raise ValueError(f"constraint check layer must be one of {sorted(CHECK_LAYERS)}")
        if self.kind not in CONSTRAINT_KINDS:
            raise ValueError(f"constraint check kind must be one of {sorted(CONSTRAINT_KINDS)}")
        if self.stage not in CHECK_STAGES:
            raise ValueError(f"constraint check stage must be one of {sorted(CHECK_STAGES)}")
        if self.status not in CHECK_STATUSES:
            raise ValueError(f"constraint check status must be one of {sorted(CHECK_STATUSES)}")
        for label in ("actual", "lower", "upper", "slack", "reference"):
            object.__setattr__(self, label, _optional_finite(getattr(self, label), label))
        tolerance = _optional_finite(self.tolerance, "tolerance")
        if tolerance is None or tolerance <= 0 or tolerance > MAX_TOLERANCE:
            raise ValueError(f"constraint check tolerance must be in (0, {MAX_TOLERANCE:g}]")
        object.__setattr__(self, "tolerance", tolerance)
        if self.status == "not_evaluated":
            if not self.reason_code:
                raise ValueError("a not_evaluated check requires reason_code")
            return
        if self.actual is None or self.slack is None:
            raise ValueError(f"a {self.status} check requires actual and slack")
        if self.lower is None and self.upper is None:
            raise ValueError(f"a {self.status} check requires lower, upper, or both")
        expected = bound_slack(self.actual, self.lower, self.upper)
        if abs(expected - self.slack) > 1e-12:
            raise ValueError("constraint check slack does not match actual and bounds")
        within = self.slack >= -self.tolerance
        if self.status == "pass" and not within:
            raise ValueError("a passing check must have slack >= -tolerance")
        if self.status in {"fail", "improved"} and within:
            raise ValueError(f"a {self.status} check must have slack < -tolerance")
        if self.status == "improved" and self.reference is None:
            raise ValueError("an improved check requires the pre-trade reference value")

    @classmethod
    def from_bounds(
        cls,
        *,
        actual: float,
        lower: float | None,
        upper: float | None,
        tolerance: float,
        **fields: Any,
    ) -> ConstraintCheck:
        """Build a pass/fail check whose slack follows the contract definition."""

        slack = bound_slack(float(actual), lower, upper)
        return cls(
            actual=actual,
            lower=lower,
            upper=upper,
            tolerance=tolerance,
            slack=slack,
            status="pass" if slack >= -tolerance else "fail",
            **fields,
        )

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


__all__ = [
    "BENCHMARK_SUM_TOLERANCE",
    "CONSTRAINT_KINDS",
    "DEFAULT_TOLERANCE",
    "ERROR_CODES",
    "MAX_TOLERANCE",
    "SCHEMA_VERSION",
    "UNCLASSIFIED_GROUP",
    "BenchmarkDeviationConstraint",
    "BenchmarkSnapshot",
    "ClassificationSnapshot",
    "ConstraintCheck",
    "GroupWeightConstraint",
    "PortfolioConstraint",
    "PortfolioConstraintData",
    "PortfolioConstraintError",
    "PortfolioConstraintSet",
    "RiskBudgetConstraint",
    "RiskModelSpec",
    "bound_slack",
    "canonical_json",
    "validate_constraint_data",
]
