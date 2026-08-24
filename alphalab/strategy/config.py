"""Internal structured settings used by the guarded backtest core."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

FACTOR_SOURCES = ("technical", "fundamental", "expression")


@dataclass(frozen=True)
class UniverseSpec:
    """Tradable universe definition."""

    pool: str = "all"
    symbols: tuple[str, ...] = ()
    min_price: float = 0.0
    min_history_days: int = 60
    min_average_amount: float = 0.0
    max_stale_days: int = 7
    require_positive_volume: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "symbols",
            tuple(str(symbol).strip().upper() for symbol in self.symbols if str(symbol).strip()),
        )
        if not math.isfinite(self.min_price) or self.min_price < 0:
            raise ValueError("universe.min_price must be non-negative")
        if self.min_history_days < 2:
            raise ValueError("universe.min_history_days must be at least 2")
        if not math.isfinite(self.min_average_amount) or self.min_average_amount < 0:
            raise ValueError("universe.min_average_amount must be non-negative")
        if self.max_stale_days < 0:
            raise ValueError("universe.max_stale_days must be non-negative")


@dataclass(frozen=True)
class FactorSpec:
    """One cross-sectional factor in the composite score."""

    name: str
    weight: float
    direction: str = "long"
    source: str = "technical"
    expression: str | None = None
    winsorize: float = 0.01
    neutralize: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("factor.name must not be empty")
        if self.direction not in {"long", "short"}:
            raise ValueError("factor.direction must be 'long' or 'short'")
        if self.source not in FACTOR_SOURCES:
            raise ValueError(f"factor.source must be one of {FACTOR_SOURCES}")
        if not math.isfinite(self.weight) or self.weight < 0:
            raise ValueError("factor.weight must be non-negative")
        object.__setattr__(
            self,
            "neutralize",
            tuple(str(value).strip().lower() for value in self.neutralize if str(value).strip()),
        )
        if not math.isfinite(self.winsorize) or not 0 <= self.winsorize < 0.25:
            raise ValueError("factor.winsorize must be in [0, 0.25)")
        unknown_neutralizers = sorted(set(self.neutralize) - {"market_cap"})
        if unknown_neutralizers:
            raise ValueError(f"unsupported factor neutralizers: {unknown_neutralizers}")
        from alphalab.factors.fundamental import FundamentalFactors
        from alphalab.factors.technical import TechnicalFactors

        technical = set(TechnicalFactors().available_factors)
        fundamental = set(FundamentalFactors.available_factors)
        if self.source == "expression":
            from alphalab.factors.expression import (
                FUNDAMENTAL_DATA_FIELDS,
                MARKET_DATA_FIELDS,
                factor_dependencies,
            )

            if not self.expression:
                raise ValueError("expression factors require factor.expression")
            dependencies = factor_dependencies(self.expression)
            available = technical | fundamental | set(MARKET_DATA_FIELDS) | set(FUNDAMENTAL_DATA_FIELDS)
            unknown = sorted(set(dependencies) - available)
            if unknown:
                raise ValueError(f"unknown factor expression inputs: {unknown}")
        elif self.expression is not None:
            raise ValueError("factor.expression is only valid when source is expression")
        elif self.source == "technical" and self.name not in technical:
            raise ValueError(f"unknown technical factor: {self.name}")
        elif self.source == "fundamental" and self.name not in fundamental:
            raise ValueError(f"unknown fundamental factor: {self.name}")


@dataclass(frozen=True)
class SelectionSpec:
    """Cross-sectional signal-model controls."""

    min_factor_coverage: float = 0.5
    n_stocks: int = 10
    signal_frequency: str = "monthly"
    normalization: str = "percentile_rank"
    factor_weights: dict[str, float] = field(default_factory=dict)
    exit_rank: int | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_factor_coverage) or not 0 <= self.min_factor_coverage <= 1:
            raise ValueError("selection.min_factor_coverage must be in [0, 1]")
        if self.n_stocks < 1:
            raise ValueError("selection.n_stocks must be >= 1")
        if self.signal_frequency not in {"daily", "weekly", "monthly"}:
            raise ValueError("selection.signal_frequency must be daily, weekly, or monthly")
        if self.normalization not in {"percentile_rank", "zscore"}:
            raise ValueError("selection.normalization must be percentile_rank or zscore")
        normalized_weights: dict[str, float] = {}
        for raw_name, raw_weight in self.factor_weights.items():
            name = str(raw_name).strip()
            weight = float(raw_weight)
            if not name:
                raise ValueError("selection.factor_weights keys must not be empty")
            if not math.isfinite(weight) or weight < 0:
                raise ValueError("selection.factor_weights values must be non-negative")
            normalized_weights[name] = weight
        object.__setattr__(self, "factor_weights", normalized_weights)
        if self.exit_rank is not None:
            object.__setattr__(self, "exit_rank", int(self.exit_rank))
            if self.exit_rank < self.n_stocks:
                raise ValueError("selection.exit_rank must be >= selection.n_stocks")

    @property
    def effective_exit_rank(self) -> int:
        """Rank below which an existing holding may remain in the signal set."""

        return self.exit_rank if self.exit_rank is not None else self.n_stocks


@dataclass(frozen=True)
class PortfolioSpec:
    """Portfolio construction controls."""

    max_weight: float = 0.10
    max_gross_exposure: float = 1.0
    optimizer: str = "equal_weight"
    rank_decay: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_weight) or self.max_weight <= 0:
            raise ValueError("portfolio.max_weight must be positive")
        if not math.isfinite(self.max_gross_exposure) or not 0 < self.max_gross_exposure <= 1:
            raise ValueError("portfolio.max_gross_exposure must be in (0, 1]")
        if self.optimizer not in {"equal_weight", "score_weight", "rank_decay"}:
            raise ValueError("portfolio.optimizer must be equal_weight, score_weight, or rank_decay")
        if not math.isfinite(self.rank_decay) or self.rank_decay < 0:
            raise ValueError("portfolio.rank_decay must be non-negative")


@dataclass(frozen=True)
class ExecutionSpec:
    """Backtest execution assumptions."""

    cost_bps: float = 20.0
    slippage_bps: float = 0.0
    impact_bps: float = 0.0
    execution_price: str = "next_open"
    portfolio_value: float = 1_000_000.0
    max_participation_rate: float = 0.10

    def __post_init__(self) -> None:
        for name in ("cost_bps", "slippage_bps", "impact_bps"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"execution.{name} must be non-negative")
        if self.execution_price not in {"next_open", "next_close"}:
            raise ValueError("execution.execution_price must be next_open or next_close")
        if not math.isfinite(self.portfolio_value) or self.portfolio_value <= 0:
            raise ValueError("execution.portfolio_value must be positive")
        if not math.isfinite(self.max_participation_rate) or not 0 < self.max_participation_rate <= 1:
            raise ValueError("execution.max_participation_rate must be in (0, 1]")


@dataclass(frozen=True)
class StrategyConfig:
    """Complete strategy definition."""

    name: str
    description: str = ""
    universe: UniverseSpec = field(default_factory=UniverseSpec)
    factors: tuple[FactorSpec, ...] = ()
    selection: SelectionSpec = field(default_factory=SelectionSpec)
    portfolio: PortfolioSpec = field(default_factory=PortfolioSpec)
    execution: ExecutionSpec = field(default_factory=ExecutionSpec)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StrategyConfig":
        """Build a strategy from the structured public representation."""
        raw = dict(raw)
        universe_raw = dict(raw.get("universe", {}))
        selection_raw = dict(raw.get("selection", {}))
        execution_raw = dict(raw.get("execution", {}))
        # Before signal models owned the decision calendar, persisted projects
        # stored it under execution.  Accept that exact historical shape while
        # emitting only the current contract from ``to_dict``.
        legacy_frequency = execution_raw.pop("rebalance_freq", None)
        if "signal_frequency" not in selection_raw and legacy_frequency is not None:
            selection_raw["signal_frequency"] = legacy_frequency
        if universe_raw.get("symbols") is None:
            universe_raw["symbols"] = []
        return cls(
            name=str(raw.get("name", "unnamed")),
            description=str(raw.get("description", "")),
            universe=UniverseSpec(**universe_raw),
            factors=tuple(FactorSpec(**item) for item in raw.get("factors", [])),
            selection=SelectionSpec(**selection_raw),
            portfolio=PortfolioSpec(**raw.get("portfolio", {})),
            execution=ExecutionSpec(**execution_raw),
            metadata=dict(raw.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the internal JSON-compatible settings representation."""
        payload = {
            "name": self.name,
            "description": self.description,
            "universe": {**asdict(self.universe), "symbols": list(self.universe.symbols)},
            "factors": [asdict(factor) for factor in self.factors],
            "selection": asdict(self.selection),
            "portfolio": asdict(self.portfolio),
            "execution": asdict(self.execution),
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload

    @property
    def factor_names(self) -> list[str]:
        return [factor.name for factor in self.factors]

    def effective_factor_weight(self, factor: FactorSpec) -> float:
        return float(self.selection.factor_weights.get(factor.name, factor.weight))

    @property
    def active_factor_names(self) -> list[str]:
        return [factor.name for factor in self.factors if self.effective_factor_weight(factor) > 0]

    @property
    def total_weight(self) -> float:
        return float(
            sum(
                self.effective_factor_weight(factor)
                for factor in self.factors
            )
        )

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if self.factors and self.total_weight <= 0:
            warnings.append("Factor weights must sum to a positive value")
        unknown_weights = sorted(set(self.selection.factor_weights) - set(self.factor_names))
        if unknown_weights:
            warnings.append(f"Signal weights reference unknown factors: {unknown_weights}")
        if self.selection.n_stocks * self.portfolio.max_weight < self.portfolio.max_gross_exposure:
            warnings.append("n_stocks * max_weight is below target gross exposure; portfolio will hold extra cash")
        return warnings

