"""Execution risk checks."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskGuardConfig:
    max_order_value: float = 100_000.0
    max_weight: float = 0.20


@dataclass(frozen=True)
class RiskViolation:
    field: str
    message: str


class RiskGuard:
    def __init__(self, config: RiskGuardConfig | None = None):
        self.config = config or RiskGuardConfig()

    def validate_order(self, symbol: str, quantity: float, price: float) -> list[RiskViolation]:
        value = abs(float(quantity) * float(price))
        if value > self.config.max_order_value:
            return [RiskViolation("max_order_value", f"{symbol} order value {value:.2f} exceeds limit")]
        return []

