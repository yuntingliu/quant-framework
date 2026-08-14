"""Strategy implementation metadata shared by selection and timing configs."""
from __future__ import annotations

import math
from dataclasses import dataclass

STRATEGY_IMPLEMENTATION_KINDS = ("configured", "python")


@dataclass(frozen=True)
class StrategyImplementationSpec:
    """Choose the built-in configured engine or a trusted local Python hook."""

    kind: str = "configured"
    entrypoint: str = "generate"
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.kind not in STRATEGY_IMPLEMENTATION_KINDS:
            raise ValueError(
                f"implementation.kind must be one of {STRATEGY_IMPLEMENTATION_KINDS}"
            )
        if not self.entrypoint.isidentifier() or self.entrypoint.startswith("_"):
            raise ValueError("implementation.entrypoint must be a public Python identifier")
        if not math.isfinite(self.timeout_seconds) or not 0.1 <= self.timeout_seconds <= 30:
            raise ValueError("implementation.timeout_seconds must be between 0.1 and 30")


__all__ = ["STRATEGY_IMPLEMENTATION_KINDS", "StrategyImplementationSpec"]
