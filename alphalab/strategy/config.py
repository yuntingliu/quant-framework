"""YAML strategy configuration for the barebone research loop."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from alphalab.strategy.implementation import StrategyImplementationSpec

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
            from alphalab.factors.expression import factor_dependencies

            if not self.expression:
                raise ValueError("expression factors require factor.expression")
            dependencies = factor_dependencies(self.expression)
            available = technical | fundamental
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
    """Selection controls after factor ranking."""

    min_factor_coverage: float = 0.5
    n_stocks: int = 10

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_factor_coverage) or not 0 <= self.min_factor_coverage <= 1:
            raise ValueError("selection.min_factor_coverage must be in [0, 1]")
        if self.n_stocks < 1:
            raise ValueError("selection.n_stocks must be >= 1")


@dataclass(frozen=True)
class PortfolioSpec:
    """Portfolio construction controls."""

    max_weight: float = 0.10
    rebalance_freq: str = "monthly"
    optimizer: str = "equal_weight"

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_weight) or self.max_weight <= 0:
            raise ValueError("portfolio.max_weight must be positive")
        if self.rebalance_freq not in {"monthly", "weekly"}:
            raise ValueError("portfolio.rebalance_freq must be monthly or weekly")
        if self.optimizer != "equal_weight":
            raise ValueError("barebone only ships the equal_weight optimizer")


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
    implementation: StrategyImplementationSpec = field(
        default_factory=StrategyImplementationSpec
    )
    metadata: dict[str, Any] = field(default_factory=dict)
    _source_path: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "StrategyConfig":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls._from_dict(raw, source_path=str(path))

    @classmethod
    def from_yaml_string(cls, text: str) -> "StrategyConfig":
        return cls._from_dict(yaml.safe_load(text) or {})

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StrategyConfig":
        """Build a strategy from the structured public representation."""
        return cls._from_dict(dict(raw))

    @classmethod
    def _from_dict(cls, raw: dict[str, Any], source_path: str | None = None) -> "StrategyConfig":
        strategy_type = str(raw.get("strategy_type", "stock_selection"))
        if strategy_type != "stock_selection":
            raise ValueError("strategy_type must be stock_selection")
        universe_raw = dict(raw.get("universe", {}))
        if universe_raw.get("symbols") is None:
            universe_raw["symbols"] = []
        return cls(
            name=str(raw.get("name", "unnamed")),
            description=str(raw.get("description", "")),
            universe=UniverseSpec(**universe_raw),
            factors=tuple(FactorSpec(**item) for item in raw.get("factors", [])),
            selection=SelectionSpec(**raw.get("selection", {})),
            portfolio=PortfolioSpec(**raw.get("portfolio", {})),
            execution=ExecutionSpec(**raw.get("execution", {})),
            implementation=StrategyImplementationSpec(
                **raw.get("implementation", {})
            ),
            metadata=dict(raw.get("metadata", {})),
            _source_path=source_path,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON/YAML-compatible strategy representation."""
        payload = {
            "strategy_type": "stock_selection",
            "name": self.name,
            "description": self.description,
            "universe": {**asdict(self.universe), "symbols": list(self.universe.symbols)},
            "factors": [asdict(factor) for factor in self.factors],
            "selection": asdict(self.selection),
            "portfolio": asdict(self.portfolio),
            "execution": asdict(self.execution),
            "implementation": asdict(self.implementation),
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload

    def to_yaml(self) -> str:
        payload = self.to_dict()
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

    @property
    def factor_names(self) -> list[str]:
        return [factor.name for factor in self.factors]

    @property
    def total_weight(self) -> float:
        return float(sum(factor.weight for factor in self.factors))

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if self.implementation.kind == "configured" and not self.factors:
            warnings.append("No factors defined")
        if self.implementation.kind == "configured" and self.total_weight <= 0:
            warnings.append("Factor weights must sum to a positive value")
        if self.selection.n_stocks * self.portfolio.max_weight < 1:
            warnings.append("n_stocks * max_weight is below 100%; portfolio will hold cash")
        return warnings

