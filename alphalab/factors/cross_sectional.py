"""Cross-sectional factor calculation shared by signals and research diagnostics."""
from __future__ import annotations

import numpy as np
import pandas as pd

from alphalab.factors.expression import evaluate_factor_expression, factor_dependencies
from alphalab.factors.fundamental import FundamentalFactors
from alphalab.factors.technical import TechnicalFactors
from alphalab.strategy.config import FactorSpec


def factor_input_names(factor: FactorSpec) -> tuple[str, ...]:
    if factor.source == "expression":
        return factor_dependencies(factor.expression or "")
    return (factor.name,)


def required_fundamental_fields(factors: list[FactorSpec] | tuple[FactorSpec, ...]) -> list[str]:
    available = set(FundamentalFactors.available_factors)
    fields: set[str] = set()
    for factor in factors:
        fields.update(name for name in factor_input_names(factor) if name in available)
        fields.update(factor.neutralize)
    return sorted(fields)


def compute_cross_sectional_factor(
    factor: FactorSpec,
    data_by_symbol: dict[str, pd.DataFrame],
    fundamentals: pd.DataFrame,
) -> pd.Series:
    """Compute, winsorize, and optionally neutralize one factor snapshot."""

    technical = TechnicalFactors()
    fundamental = FundamentalFactors()
    inputs: dict[str, pd.Series] = {}
    for name in factor_input_names(factor):
        if name in technical.available_factors:
            inputs[name] = technical.compute(name, data_by_symbol)
        elif name in FundamentalFactors.available_factors:
            inputs[name] = fundamental.compute(name, fundamentals)
        else:
            raise KeyError(f"Unknown base factor: {name}")
    if factor.source == "expression":
        values = evaluate_factor_expression(factor.expression or "", inputs)
    else:
        values = inputs[factor.name]
    values = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return pd.Series(dtype=float, name=factor.name)
    if factor.winsorize > 0 and len(values) >= 4:
        lower = float(values.quantile(factor.winsorize))
        upper = float(values.quantile(1.0 - factor.winsorize))
        values = values.clip(lower=lower, upper=upper)
    if factor.neutralize:
        values = _neutralize(values, fundamentals, factor.neutralize)
    return values.rename(factor.name).dropna()


def _neutralize(
    values: pd.Series,
    fundamentals: pd.DataFrame,
    neutralizers: tuple[str, ...],
) -> pd.Series:
    if fundamentals.empty:
        raise ValueError("factor neutralization requires point-in-time fundamentals")
    latest = fundamentals.copy()
    sort_columns = [column for column in ("available_date", "quarter") if column in latest]
    if sort_columns:
        latest = latest.sort_values(sort_columns)
    latest = latest.groupby("symbol").tail(1).set_index("symbol")
    design = pd.DataFrame(index=values.index)
    for name in neutralizers:
        if name != "market_cap" or name not in latest:
            raise ValueError(f"factor neutralization requires {name}")
        raw = pd.to_numeric(latest[name], errors="coerce").reindex(values.index)
        design[name] = np.log(raw.where(raw > 0))
    aligned = pd.concat([values.rename("factor"), design], axis=1).dropna()
    if len(aligned) <= len(neutralizers) + 1:
        raise ValueError("factor neutralization has insufficient cross-sectional coverage")
    matrix = np.column_stack(
        [np.ones(len(aligned)), *[aligned[name].to_numpy(dtype=float) for name in neutralizers]]
    )
    coefficients, *_ = np.linalg.lstsq(
        matrix,
        aligned["factor"].to_numpy(dtype=float),
        rcond=None,
    )
    residuals = aligned["factor"].to_numpy(dtype=float) - matrix @ coefficients
    return pd.Series(residuals, index=aligned.index, name=values.name)


__all__ = [
    "compute_cross_sectional_factor",
    "factor_input_names",
    "required_fundamental_fields",
]
