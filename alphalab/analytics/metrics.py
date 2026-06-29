"""Performance metric helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd


class PerformanceMetrics:
    """Compute standard return-series metrics."""

    @staticmethod
    def summarize(returns: pd.Series, periods_per_year: int = 12) -> dict[str, float | int]:
        returns = pd.Series(returns, dtype=float).dropna()
        if returns.empty:
            return {
                "total_return": 0.0,
                "annual_return": 0.0,
                "annual_vol": 0.0,
                "sharpe": 0.0,
                "max_drawdown": 0.0,
                "n_periods": 0,
            }
        nav = (1 + returns).cumprod()
        total = float(nav.iloc[-1] - 1)
        years = max(len(returns) / periods_per_year, 1 / periods_per_year)
        annual_return = float((1 + total) ** (1 / years) - 1)
        annual_vol = float(returns.std(ddof=0) * np.sqrt(periods_per_year))
        sharpe = float(annual_return / annual_vol) if annual_vol > 0 else 0.0
        max_drawdown = float((nav / nav.cummax() - 1).min())
        return {
            "total_return": total,
            "annual_return": annual_return,
            "annual_vol": annual_vol,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "n_periods": int(len(returns)),
        }

