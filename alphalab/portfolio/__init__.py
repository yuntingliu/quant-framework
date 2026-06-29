"""Portfolio construction helpers."""
from __future__ import annotations

import pandas as pd


class EqualWeightOptimizer:
    """Long-only equal weight optimizer with a per-name cap."""

    def __init__(self, max_weight: float = 0.10):
        if max_weight <= 0:
            raise ValueError("max_weight must be positive")
        self.max_weight = max_weight

    def optimize(self, symbols: list[str]) -> pd.Series:
        if not symbols:
            return pd.Series(dtype=float)
        raw = pd.Series(1.0, index=[str(symbol).upper() for symbol in symbols], dtype=float)
        weights = raw / raw.sum()
        weights = weights.clip(upper=self.max_weight)
        return weights / weights.sum() if weights.sum() > 0 else weights


__all__ = ["EqualWeightOptimizer"]

