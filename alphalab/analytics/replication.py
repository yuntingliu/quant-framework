"""Arithmetic audit of reported annual returns; not a factor backtest."""

from __future__ import annotations

import numpy as np
import pandas as pd


def audit_annual_return_table(returns: pd.DataFrame, *, benchmark: str) -> pd.DataFrame:
    """Audit a complete consecutive-year table of decimal returns.

    Daily volatility, drawdown, holdings, and factor exposures cannot be reconstructed
    from annual returns. A cumulative curve from this table is a transcription audit,
    not independent empirical replication.
    """
    if returns.empty or benchmark not in returns or returns.columns.has_duplicates:
        raise ValueError("Expected a non-empty table with a unique benchmark column")
    years = pd.to_numeric(returns.index, errors="raise").to_numpy(dtype=float)
    if not np.isfinite(years).all() or not np.equal(years, np.floor(years)).all():
        raise ValueError("Index must contain integer calendar years")
    if len(np.unique(years)) != len(years) or sorted(years) != list(range(int(min(years)), int(max(years)) + 1)):
        raise ValueError("Expected one complete row per consecutive calendar year")
    data = returns.apply(pd.to_numeric, errors="raise").astype(float)
    if not np.isfinite(data.to_numpy()).all() or (data < -1).any().any():
        raise ValueError("Returns must be finite decimals no lower than -100%")
    wealth = (1 + data).prod()
    result = pd.DataFrame({"years": len(data), "cumulative_return": wealth - 1,
                           "calendar_cagr": wealth ** (1 / len(data)) - 1,
                           "annual_win_rate_vs_benchmark": data.gt(data[benchmark], axis=0).mean()})
    result.loc[benchmark, "annual_win_rate_vs_benchmark"] = np.nan
    result.index.name = "series"
    return result
