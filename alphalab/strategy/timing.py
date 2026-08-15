"""Market-timing strategy configuration."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from alphalab.strategy.pipeline import StrategyPipelineSpec

TIMING_SIGNAL_KINDS = ("trend", "momentum", "volatility_control")


@dataclass(frozen=True)
class TimingSignalSpec:
    """One normalized time-series signal contributing to market exposure."""

    kind: str
    weight: float = 1.0
    window: int = 6
    threshold: float = 0.0

    def __post_init__(self) -> None:
        if self.kind not in TIMING_SIGNAL_KINDS:
            raise ValueError(f"timing signal kind must be one of {TIMING_SIGNAL_KINDS}")
        if not math.isfinite(self.weight) or self.weight < 0:
            raise ValueError("timing signal weight must be non-negative")
        if self.window < 2 or self.window > 120:
            raise ValueError("timing signal window must be between 2 and 120 periods")
        if not math.isfinite(self.threshold):
            raise ValueError("timing signal threshold must be finite")
        if self.kind == "volatility_control" and self.threshold <= 0:
            raise ValueError("volatility_control threshold must be a positive annual volatility")


@dataclass(frozen=True)
class TimingPositionSpec:
    """Map the combined signal score into portfolio exposure."""

    min_exposure: float = 0.0
    max_exposure: float = 1.0

    def __post_init__(self) -> None:
        for name in ("min_exposure", "max_exposure"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"position.{name} must be in [0, 1]")
        if self.min_exposure > self.max_exposure:
            raise ValueError("position.min_exposure must not exceed max_exposure")


@dataclass(frozen=True)
class TimingExecutionSpec:
    """Turnover cost applied when the market exposure changes."""

    cost_bps: float = 5.0
    slippage_bps: float = 0.0

    def __post_init__(self) -> None:
        for name in ("cost_bps", "slippage_bps"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"execution.{name} must be non-negative")


@dataclass(frozen=True)
class TimingStrategyConfig:
    """A monthly portfolio-level market-timing strategy."""

    name: str
    description: str = ""
    market_factor: str = "MKT"
    signals: tuple[TimingSignalSpec, ...] = (
        TimingSignalSpec(kind="trend", weight=1.0, window=12, threshold=0.0),
    )
    position: TimingPositionSpec = field(default_factory=TimingPositionSpec)
    execution: TimingExecutionSpec = field(default_factory=TimingExecutionSpec)
    pipeline: StrategyPipelineSpec = field(default_factory=StrategyPipelineSpec)
    metadata: dict[str, Any] = field(default_factory=dict)
    _source_path: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TimingStrategyConfig":
        return cls._from_dict(dict(raw))

    @classmethod
    def _from_dict(
        cls,
        raw: dict[str, Any],
        source_path: str | None = None,
    ) -> "TimingStrategyConfig":
        strategy_type = str(raw.get("strategy_type", "market_timing"))
        if strategy_type != "market_timing":
            raise ValueError("strategy_type must be market_timing")
        signal_rows = raw.get("signals")
        signals = (
            cls.__dataclass_fields__["signals"].default
            if signal_rows is None
            else tuple(TimingSignalSpec(**item) for item in signal_rows)
        )
        return cls(
            name=str(raw.get("name", "unnamed")),
            description=str(raw.get("description", "")),
            market_factor=str(raw.get("market_factor", "MKT")).strip().upper(),
            signals=signals,
            position=TimingPositionSpec(**raw.get("position", {})),
            execution=TimingExecutionSpec(**raw.get("execution", {})),
            pipeline=StrategyPipelineSpec.from_dict(
                raw.get("pipeline"),
                legacy_implementation=raw.get("implementation"),
            ),
            metadata=dict(raw.get("metadata", {})),
            _source_path=source_path,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "strategy_type": "market_timing",
            "name": self.name,
            "description": self.description,
            "market_factor": self.market_factor,
            "signals": [asdict(signal) for signal in self.signals],
            "position": asdict(self.position),
            "execution": asdict(self.execution),
            "pipeline": self.pipeline.to_dict(),
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload

    @property
    def total_weight(self) -> float:
        return float(sum(signal.weight for signal in self.signals))

    @property
    def signal_names(self) -> list[str]:
        return [signal.kind for signal in self.signals]

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if self.market_factor != "MKT":
            warnings.append("Barebone timing research currently requires the MKT series")
        needs_registered_signal = (
            self.pipeline.signal.kind == "configured"
            and self.pipeline.portfolio.kind != "python"
        )
        if needs_registered_signal and not self.signals:
            warnings.append("No timing signals defined")
        if needs_registered_signal and self.total_weight <= 0:
            warnings.append("Timing signal weights must sum to a positive value")
        return warnings


__all__ = [
    "TIMING_SIGNAL_KINDS",
    "TimingExecutionSpec",
    "TimingPositionSpec",
    "TimingSignalSpec",
    "TimingStrategyConfig",
]
