"""YAML strategy configuration for the barebone research loop."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

FACTOR_SOURCES = ("technical", "fundamental")


@dataclass(frozen=True)
class UniverseSpec:
    """Tradable universe definition."""

    pool: str = "all"
    symbols: tuple[str, ...] = ()
    min_price: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "symbols",
            tuple(str(symbol).strip().upper() for symbol in self.symbols if str(symbol).strip()),
        )


@dataclass(frozen=True)
class FactorSpec:
    """One cross-sectional factor in the composite score."""

    name: str
    weight: float
    direction: str = "long"
    source: str = "technical"

    def __post_init__(self) -> None:
        if self.direction not in {"long", "short"}:
            raise ValueError("factor.direction must be 'long' or 'short'")
        if self.source not in FACTOR_SOURCES:
            raise ValueError(f"factor.source must be one of {FACTOR_SOURCES}")
        if self.weight < 0:
            raise ValueError("factor.weight must be non-negative")


@dataclass(frozen=True)
class SelectionSpec:
    """Selection controls after factor ranking."""

    min_factor_coverage: float = 0.5
    n_stocks: int = 10

    def __post_init__(self) -> None:
        if not 0 <= self.min_factor_coverage <= 1:
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
        if self.max_weight <= 0:
            raise ValueError("portfolio.max_weight must be positive")
        if self.rebalance_freq not in {"monthly", "weekly"}:
            raise ValueError("portfolio.rebalance_freq must be monthly or weekly")
        if self.optimizer != "equal_weight":
            raise ValueError("barebone only ships the equal_weight optimizer")


@dataclass(frozen=True)
class ExecutionSpec:
    """Backtest execution assumptions."""

    cost_bps: float = 20.0


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
    def _from_dict(cls, raw: dict[str, Any], source_path: str | None = None) -> "StrategyConfig":
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
            metadata=dict(raw.get("metadata", {})),
            _source_path=source_path,
        )

    def to_yaml(self) -> str:
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
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

    @property
    def factor_names(self) -> list[str]:
        return [factor.name for factor in self.factors]

    @property
    def total_weight(self) -> float:
        return float(sum(factor.weight for factor in self.factors))

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if not self.factors:
            warnings.append("No factors defined")
        if self.total_weight <= 0:
            warnings.append("Factor weights must sum to a positive value")
        if self.selection.n_stocks * self.portfolio.max_weight < 1:
            warnings.append("n_stocks * max_weight is below 100%; portfolio will hold cash")
        return warnings

