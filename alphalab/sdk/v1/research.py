"""Stable research helpers available to Strategy SDK v1 projects."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.optimize import minimize
from scipy.spatial.distance import squareform


class PortfolioOptimizationError(ValueError):
    """Raised when a requested portfolio cannot be estimated or constrained."""


@dataclass(frozen=True)
class PortfolioOptimizationResult:
    """Long-only target weights plus auditable numerical diagnostics."""

    weights: Mapping[str, float]
    diagnostics: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "weights", MappingProxyType(dict(self.weights)))
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))


@dataclass(frozen=True)
class FactorNeutralizationResult:
    """Cross-sectional residual scores and the fitted exposure diagnostics."""

    values: pd.Series
    diagnostics: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", pd.Series(self.values).copy())
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))


def neutralize_factor_scores(
    values: pd.Series,
    exposures: pd.DataFrame,
    *,
    weights: pd.Series | Mapping[str, float] | None = None,
    add_intercept: bool = True,
    minimum_observations: int | None = None,
) -> FactorNeutralizationResult:
    """Residualize one factor cross-section against contemporaneous exposures.

    Rows with missing scores or exposures are excluded rather than imputed.  The
    returned series retains the original score index and uses ``NaN`` for rows
    that could not enter the point-in-time regression.
    """

    score = pd.to_numeric(pd.Series(values), errors="coerce").rename("score")
    frame = pd.DataFrame(exposures).apply(pd.to_numeric, errors="coerce")
    if frame.empty or not len(frame.columns):
        raise ValueError("neutralization requires at least one exposure column")
    if frame.columns.duplicated().any():
        raise ValueError("neutralization exposure names must be unique")
    aligned = pd.concat([score, frame], axis=1, join="inner")
    weight_name = None
    if weights is not None:
        weight_name = "__weight__"
        aligned[weight_name] = pd.to_numeric(pd.Series(weights), errors="coerce")
    clean = aligned.replace([np.inf, -np.inf], np.nan).dropna()
    if weight_name is not None:
        clean = clean.loc[clean[weight_name].gt(0)]
    parameter_count = len(frame.columns) + int(add_intercept)
    required = max(parameter_count + 2, int(minimum_observations or 0))
    if len(clean) < required:
        raise ValueError(
            f"neutralization requires at least {required} complete observations; got {len(clean)}"
        )

    x = clean[list(frame.columns)].to_numpy(dtype=float)
    names = [str(name) for name in frame.columns]
    if add_intercept:
        x = np.column_stack([np.ones(len(clean)), x])
        names = ["intercept", *names]
    y = clean["score"].to_numpy(dtype=float)
    if weight_name is not None:
        root_weight = np.sqrt(clean[weight_name].to_numpy(dtype=float))
        fit_x = x * root_weight[:, None]
        fit_y = y * root_weight
    else:
        fit_x, fit_y = x, y
    coefficients, _, rank, _ = np.linalg.lstsq(fit_x, fit_y, rcond=None)
    if rank < x.shape[1]:
        raise ValueError("neutralization exposure matrix is rank deficient")
    residuals = y - x @ coefficients
    output = pd.Series(np.nan, index=score.index, dtype=float, name=score.name)
    output.loc[clean.index] = residuals
    total = float(np.sum((y - y.mean()) ** 2))
    residual_sum = float(np.sum(residuals**2))
    return FactorNeutralizationResult(
        values=output,
        diagnostics={
            "observations": int(len(clean)),
            "excluded_observations": int(len(score) - len(clean)),
            "exposures": [str(name) for name in frame.columns],
            "coefficients": {
                name: float(coefficients[index]) for index, name in enumerate(names)
            },
            "r_squared": float(1.0 - residual_sum / total) if total > 0 else 0.0,
            "weighted": weight_name is not None,
        },
    )


def optimize_portfolio(
    returns: pd.DataFrame | None = None,
    *,
    method: str = "equal_weight",
    symbols: Sequence[str] | None = None,
    expected_returns: pd.Series | Mapping[str, float] | None = None,
    current_weights: pd.Series | Mapping[str, float] | None = None,
    target_gross: float = 1.0,
    max_weight: float = 1.0,
    max_turnover: float | None = None,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
    minimum_observations: int = 60,
) -> PortfolioOptimizationResult:
    """Construct a deterministic long-only portfolio under core-compatible limits.

    Supported methods are ``equal_weight``, ``minimum_variance``,
    ``risk_parity``, ``hrp``, and ``max_sharpe``.  ``max_sharpe`` deliberately
    requires explicit expected returns; the helper never invents an alpha
    forecast from an in-sample mean or silently falls back to another method.
    """

    method = str(method).strip().lower()
    allowed = {"equal_weight", "minimum_variance", "risk_parity", "hrp", "max_sharpe"}
    if method not in allowed:
        raise PortfolioOptimizationError(f"unsupported portfolio method: {method}")
    target_gross = _finite_scalar(target_gross, "target_gross")
    max_weight = _finite_scalar(max_weight, "max_weight")
    if target_gross <= 0 or target_gross > 1:
        raise PortfolioOptimizationError("target_gross must be in (0, 1]")
    if max_weight <= 0 or max_weight > 1:
        raise PortfolioOptimizationError("max_weight must be in (0, 1]")
    if max_turnover is not None:
        max_turnover = _finite_scalar(max_turnover, "max_turnover")
        if max_turnover < 0 or max_turnover > 1:
            raise PortfolioOptimizationError("max_turnover must be in [0, 1]")
    if int(periods_per_year) < 1:
        raise PortfolioOptimizationError("periods_per_year must be positive")
    if int(minimum_observations) < 2:
        raise PortfolioOptimizationError("minimum_observations must be at least 2")

    frame, asset_names, dropped = _prepare_returns(
        returns,
        symbols=symbols,
        require_history=method != "equal_weight",
        minimum_observations=int(minimum_observations),
    )
    asset_count = len(asset_names)
    if not asset_count:
        raise PortfolioOptimizationError("portfolio requires at least one symbol")
    if asset_count * max_weight + 1e-12 < target_gross:
        raise PortfolioOptimizationError(
            "target_gross is infeasible for the number of symbols and max_weight"
        )
    current = _aligned_current_weights(current_weights, asset_names)
    bounds = [(0.0, max_weight) for _ in asset_names]
    initial = np.full(asset_count, target_gross / asset_count, dtype=float)
    covariance = None
    covariance_diagnostics: dict[str, Any] = {
        "covariance_adjusted": False,
        "covariance_min_eigenvalue_before": None,
    }

    if method == "equal_weight":
        candidate = initial
        solver = "closed_form"
        if max_turnover is not None:
            candidate = _project_weights(
                candidate,
                asset_names,
                current,
                target_gross,
                bounds,
                max_turnover,
            )
            solver = "SLSQP"
    else:
        covariance, covariance_diagnostics = _regularized_covariance(
            frame, int(periods_per_year)
        )
        if method == "hrp":
            candidate = _hrp_weights(covariance, asset_names) * target_gross
            candidate = _project_weights(
                candidate,
                asset_names,
                current,
                target_gross,
                bounds,
                max_turnover,
            )
            solver = "hierarchical_clustering+SLSQP_projection"
        else:
            if method == "minimum_variance":
                def objective(weight: np.ndarray) -> float:
                    return float(weight @ covariance @ weight)
            elif method == "risk_parity":
                def objective(weight: np.ndarray) -> float:
                    return _risk_parity_objective(weight, covariance)

                volatility = np.sqrt(np.maximum(np.diag(covariance), 1e-16))
                initial = target_gross * (1.0 / volatility) / np.sum(1.0 / volatility)
            else:
                if expected_returns is None:
                    raise PortfolioOptimizationError(
                        "max_sharpe requires explicit expected_returns"
                    )
                forecasts = _aligned_expected_returns(expected_returns, asset_names)
                annual_rf = _finite_scalar(risk_free_rate, "risk_free_rate")

                def objective(weight: np.ndarray) -> float:
                    volatility = math.sqrt(max(0.0, float(weight @ covariance @ weight)))
                    if volatility <= 1e-12:
                        return 1e12
                    return -float(
                        (weight @ forecasts - target_gross * annual_rf) / volatility
                    )

            candidate = _solve_weights(
                objective,
                initial,
                asset_names,
                current,
                target_gross,
                bounds,
                max_turnover,
            )
            solver = "SLSQP"

    candidate = np.asarray(candidate, dtype=float)
    _validate_solution(candidate, target_gross, max_weight)
    turnover = _turnover(candidate, asset_names, current)
    if max_turnover is not None and turnover > max_turnover + 1e-7:
        raise PortfolioOptimizationError(
            "portfolio solution violates the requested turnover limit"
        )
    risk_contributions = _risk_contributions(candidate, covariance, asset_names)
    weights = {
        symbol: float(weight)
        for symbol, weight in zip(asset_names, candidate)
        if weight > 1e-12
    }
    return PortfolioOptimizationResult(
        weights=weights,
        diagnostics={
            "method": method,
            "solver": solver,
            "converged": True,
            "observations": int(len(frame)) if frame is not None else 0,
            "symbols": list(asset_names),
            "dropped_symbols": dropped,
            "target_gross": target_gross,
            "max_weight": max_weight,
            "max_turnover": max_turnover,
            "expected_turnover": turnover,
            "risk_contributions": risk_contributions,
            **covariance_diagnostics,
        },
    )


def _prepare_returns(
    returns: pd.DataFrame | None,
    *,
    symbols: Sequence[str] | None,
    require_history: bool,
    minimum_observations: int,
) -> tuple[pd.DataFrame | None, list[str], list[str]]:
    requested = _normalized_symbols(symbols or ())
    if returns is None:
        if require_history:
            raise PortfolioOptimizationError("the selected method requires a returns matrix")
        return None, requested, []
    frame = pd.DataFrame(returns).copy()
    normalized_columns = [str(value).strip().upper() for value in frame.columns]
    if not all(normalized_columns) or len(set(normalized_columns)) != len(normalized_columns):
        raise PortfolioOptimizationError("returns columns must normalize to unique symbols")
    frame.columns = normalized_columns
    if requested:
        missing = sorted(set(requested) - set(frame.columns))
        if missing:
            raise PortfolioOptimizationError(f"returns are missing requested symbols: {missing}")
        frame = frame[requested]
    numeric = frame.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if not require_history:
        return numeric, list(numeric.columns), []
    adequate = [
        symbol for symbol in numeric.columns if int(numeric[symbol].notna().sum()) >= minimum_observations
    ]
    dropped = [symbol for symbol in numeric.columns if symbol not in adequate]
    numeric = numeric[adequate].dropna(how="any")
    if require_history and len(numeric) < minimum_observations:
        raise PortfolioOptimizationError(
            f"portfolio requires at least {minimum_observations} complete return observations; got {len(numeric)}"
        )
    return numeric, list(numeric.columns), dropped


def _normalized_symbols(values: Sequence[str]) -> list[str]:
    symbols = [str(value).strip().upper() for value in values]
    if not all(symbols) or len(set(symbols)) != len(symbols):
        raise PortfolioOptimizationError("symbols must be non-empty and unique")
    return symbols


def _regularized_covariance(
    returns: pd.DataFrame, periods_per_year: int
) -> tuple[np.ndarray, dict[str, Any]]:
    covariance = returns.cov().to_numpy(dtype=float) * float(periods_per_year)
    covariance = (covariance + covariance.T) / 2.0
    if not np.isfinite(covariance).all():
        raise PortfolioOptimizationError("covariance matrix contains non-finite values")
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    minimum_before = float(eigenvalues.min())
    floor = max(float(eigenvalues.max()) * 1e-10, 1e-12)
    adjusted = bool(minimum_before < floor)
    if adjusted:
        covariance = eigenvectors @ np.diag(np.maximum(eigenvalues, floor)) @ eigenvectors.T
        covariance = (covariance + covariance.T) / 2.0
    return covariance, {
        "covariance_adjusted": adjusted,
        "covariance_min_eigenvalue_before": minimum_before,
    }


def _aligned_current_weights(
    values: pd.Series | Mapping[str, float] | None, asset_names: Sequence[str]
) -> pd.Series:
    if values is None:
        return pd.Series(dtype=float)
    series = pd.to_numeric(pd.Series(values), errors="coerce")
    series.index = [str(value).strip().upper() for value in series.index]
    if series.index.duplicated().any() or series.isna().any() or (series < 0).any():
        raise PortfolioOptimizationError("current_weights must be finite, non-negative, and unique")
    if float(series.sum()) > 1.0 + 1e-9:
        raise PortfolioOptimizationError("current_weights gross exposure cannot exceed 1")
    return series.astype(float)


def _aligned_expected_returns(
    values: pd.Series | Mapping[str, float], asset_names: Sequence[str]
) -> np.ndarray:
    series = pd.to_numeric(pd.Series(values), errors="coerce")
    series.index = [str(value).strip().upper() for value in series.index]
    if series.index.duplicated().any():
        raise PortfolioOptimizationError("expected_returns symbols must be unique")
    aligned = series.reindex(asset_names)
    if aligned.isna().any() or not np.isfinite(aligned.to_numpy(dtype=float)).all():
        missing = aligned.index[aligned.isna()].tolist()
        raise PortfolioOptimizationError(f"expected_returns are incomplete: {missing}")
    return aligned.to_numpy(dtype=float)


def _solve_weights(
    objective: Any,
    initial: np.ndarray,
    asset_names: Sequence[str],
    current: pd.Series,
    target_gross: float,
    bounds: list[tuple[float, float]],
    max_turnover: float | None,
) -> np.ndarray:
    count = len(asset_names)
    start = np.asarray(initial, dtype=float)
    solver_bounds = list(bounds)
    equality_gradient = np.ones(count)
    constraints: list[dict[str, Any]] = []
    if max_turnover is not None:
        held = current.reindex(asset_names, fill_value=0.0).to_numpy(dtype=float)
        outside = float(current.loc[~current.index.isin(asset_names)].sum())
        cash_change = abs(target_gross - float(current.sum()))
        budget = 2.0 * max_turnover - outside - cash_change
        if budget < -1e-12:
            raise PortfolioOptimizationError("requested turnover limit is infeasible")
        budget = max(0.0, budget)
        # Auxiliary trades bound |weight - held| with linear inequalities.
        # This is the exact L1 limit, including exited positions and cash,
        # without an absolute-value kink at unchanged portfolio weights.
        start = np.concatenate([start, np.abs(start - held)])
        solver_bounds.extend([(0.0, None)] * count)
        equality_gradient = np.concatenate([np.ones(count), np.zeros(count)])
        trade_jacobian = np.vstack([
            np.hstack([-np.eye(count), np.eye(count)]),
            np.hstack([np.eye(count), np.eye(count)]),
            np.concatenate([np.zeros(count), -np.ones(count)])[None, :],
        ])

        def trade_limits(value: np.ndarray) -> np.ndarray:
            change = value[:count] - held
            trades = value[count:]
            return np.concatenate([trades - change, trades + change,
                                   [budget - float(trades.sum())]])

        constraints.append({"type": "ineq", "fun": trade_limits,
                            "jac": lambda _value: trade_jacobian})
    constraints.append({
        "type": "eq",
        "fun": lambda value: float(np.sum(value[:count]) - target_gross),
        "jac": lambda _value: equality_gradient,
    })
    result = minimize(
        lambda value: objective(value[:count]),
        start,
        method="SLSQP",
        bounds=solver_bounds,
        constraints=constraints,
        options={"maxiter": 2_000, "ftol": 1e-12},
    )
    if not result.success:
        raise PortfolioOptimizationError(f"portfolio optimization did not converge: {result.message}")
    return np.asarray(result.x[:count], dtype=float)


def _project_weights(
    candidate: np.ndarray,
    asset_names: Sequence[str],
    current: pd.Series,
    target_gross: float,
    bounds: list[tuple[float, float]],
    max_turnover: float | None,
) -> np.ndarray:
    target = np.asarray(candidate, dtype=float)
    return _solve_weights(
        lambda weight: float(np.sum((weight - target) ** 2)),
        np.full(len(asset_names), target_gross / len(asset_names), dtype=float),
        asset_names,
        current,
        target_gross,
        bounds,
        max_turnover,
    )


def _turnover(
    weights: np.ndarray, asset_names: Sequence[str], current: pd.Series
) -> float:
    target = pd.Series(np.asarray(weights, dtype=float), index=asset_names)
    all_symbols = target.index.union(current.index)
    target_aligned = target.reindex(all_symbols, fill_value=0.0)
    current_aligned = current.reindex(all_symbols, fill_value=0.0)
    target_cash = 1.0 - float(target.sum())
    current_cash = 1.0 - float(current.sum())
    return float(
        ((target_aligned - current_aligned).abs().sum() + abs(target_cash - current_cash))
        / 2.0
    )


def _risk_parity_objective(weights: np.ndarray, covariance: np.ndarray) -> float:
    variance = float(weights @ covariance @ weights)
    if variance <= 1e-20:
        return 1e12
    contributions = weights * (covariance @ weights) / variance
    target = np.full(len(weights), 1.0 / len(weights))
    return float(np.sum((contributions - target) ** 2))


def _hrp_weights(covariance: np.ndarray, asset_names: Sequence[str]) -> np.ndarray:
    if len(asset_names) == 1:
        return np.ones(1, dtype=float)
    volatility = np.sqrt(np.maximum(np.diag(covariance), 1e-16))
    correlation = covariance / np.outer(volatility, volatility)
    correlation = np.clip((correlation + correlation.T) / 2.0, -1.0, 1.0)
    np.fill_diagonal(correlation, 1.0)
    distance = np.sqrt(np.maximum(0.0, (1.0 - correlation) / 2.0))
    hierarchy = linkage(squareform(distance, checks=False), method="single")
    order = [int(index) for index in leaves_list(hierarchy)]
    allocations = pd.Series(1.0, index=order, dtype=float)
    clusters: list[list[int]] = [order]
    while clusters:
        next_clusters: list[list[int]] = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            split = len(cluster) // 2
            left, right = cluster[:split], cluster[split:]
            left_variance = _cluster_variance(covariance, left)
            right_variance = _cluster_variance(covariance, right)
            total = left_variance + right_variance
            if total <= 1e-20:
                alpha = 0.5
            else:
                alpha = 1.0 - left_variance / total
            allocations.loc[left] *= alpha
            allocations.loc[right] *= 1.0 - alpha
            next_clusters.extend([left, right])
        clusters = next_clusters
    return allocations.reindex(range(len(asset_names))).to_numpy(dtype=float)


def _cluster_variance(covariance: np.ndarray, indices: Sequence[int]) -> float:
    matrix = covariance[np.ix_(indices, indices)]
    inverse_variance = 1.0 / np.maximum(np.diag(matrix), 1e-16)
    weights = inverse_variance / inverse_variance.sum()
    return float(weights @ matrix @ weights)


def _risk_contributions(
    weights: np.ndarray,
    covariance: np.ndarray | None,
    asset_names: Sequence[str],
) -> dict[str, float | None]:
    if covariance is None:
        return {symbol: None for symbol in asset_names}
    variance = float(weights @ covariance @ weights)
    if variance <= 1e-20:
        return {symbol: None for symbol in asset_names}
    values = weights * (covariance @ weights) / variance
    return {symbol: float(value) for symbol, value in zip(asset_names, values)}


def _validate_solution(weights: np.ndarray, target_gross: float, max_weight: float) -> None:
    if not np.isfinite(weights).all() or (weights < -1e-9).any():
        raise PortfolioOptimizationError("optimizer returned invalid weights")
    if abs(float(weights.sum()) - target_gross) > 1e-7:
        raise PortfolioOptimizationError("optimizer did not satisfy target_gross")
    if float(weights.max()) > max_weight + 1e-7:
        raise PortfolioOptimizationError("optimizer did not satisfy max_weight")


def _finite_scalar(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise PortfolioOptimizationError(f"{name} must be numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioOptimizationError(f"{name} must be numeric") from exc
    if not math.isfinite(numeric):
        raise PortfolioOptimizationError(f"{name} must be finite")
    return numeric


__all__ = [
    "FactorNeutralizationResult",
    "PortfolioOptimizationError",
    "PortfolioOptimizationResult",
    "neutralize_factor_scores",
    "optimize_portfolio",
]
