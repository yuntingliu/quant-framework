"""Drawdown helpers."""
from __future__ import annotations

import pandas as pd


class DrawdownCalculator:
    """Calculate drawdowns from a NAV or return series."""

    def drawdown(self, nav: pd.Series) -> pd.Series:
        nav = pd.Series(nav, dtype=float).dropna()
        if nav.empty:
            return pd.Series(dtype=float)
        return nav / nav.cummax() - 1.0

    def max_drawdown(self, nav: pd.Series) -> float:
        dd = self.drawdown(nav)
        return float(dd.min()) if not dd.empty else 0.0

