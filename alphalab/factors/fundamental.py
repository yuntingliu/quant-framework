"""Generic fundamental factor helpers."""
from __future__ import annotations

import pandas as pd


class FundamentalFactors:
    """Compute or pass through common fundamental factors."""

    available_factors = [
        "ep",
        "bp",
        "roe",
        "roa",
        "profit_growth",
        "revenue_growth",
        "gross_margin",
        "leverage",
    ]

    def compute(self, name: str, fundamentals: pd.DataFrame) -> pd.Series:
        if name not in self.available_factors:
            raise KeyError(f"Unknown fundamental factor: {name}")
        if fundamentals.empty or name not in fundamentals.columns:
            return pd.Series(dtype=float, name=name)
        latest = fundamentals.sort_values("quarter").groupby("symbol").tail(1)
        return latest.set_index("symbol")[name].astype(float).rename(name)

    def compute_multiple(self, names: list[str], fundamentals: pd.DataFrame) -> pd.DataFrame:
        return pd.concat([self.compute(name, fundamentals) for name in names], axis=1)


def compute_fundamental_factors(fundamentals: pd.DataFrame, factors: list[str]) -> pd.DataFrame:
    return FundamentalFactors().compute_multiple(factors, fundamentals)

