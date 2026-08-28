"""Post-backtest factor attribution from frozen strategy and factor returns."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from alphalab.analytics.metrics import PerformanceMetrics

FACTOR_NAMES = ("MKT", "SMB", "HML", "MOM", "RMW")


def factor_attribution(
    returns: pd.Series,
    factor_returns: pd.DataFrame,
    *,
    executions: list[dict] | tuple[dict, ...] = (),
    research_thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate CAPM and multi-factor exposures without rerunning the strategy."""

    strategy = _monthly_returns(returns)
    factors = _monthly_factors(factor_returns)
    aligned = pd.concat([strategy.rename("strategy"), factors], axis=1, join="inner")
    required = [name for name in (*FACTOR_NAMES, "rf") if name in aligned]
    aligned = aligned[["strategy", *required]].replace([np.inf, -np.inf], np.nan)
    complete = (
        aligned.dropna(subset=["strategy", "MKT", "rf"])
        if {"MKT", "rf"}.issubset(aligned)
        else pd.DataFrame()
    )

    warnings: list[str] = []
    if complete.empty:
        warnings.append("MKT/rf factor coverage is insufficient for alpha/beta attribution")
    elif len(complete) < 24:
        warnings.append("Fewer than 24 monthly observations; regression estimates are unstable")

    capm = _regression(complete, ("MKT",)) if not complete.empty else _empty_regression(("MKT",))
    multi_names = tuple(name for name in FACTOR_NAMES if name in complete)
    multi_frame = complete.dropna(subset=list(multi_names)) if multi_names else pd.DataFrame()
    multi_factor = (
        _regression(multi_frame, multi_names)
        if not multi_frame.empty and multi_names
        else _empty_regression(multi_names)
    )
    if capm.get("warning"):
        warnings.append(str(capm["warning"]))
    if multi_factor.get("warning"):
        warnings.append(str(multi_factor["warning"]))

    correlation_frame = factors[[name for name in FACTOR_NAMES if name in factors]].dropna(
        how="all"
    )
    factor_correlation = {
        "labels": list(correlation_frame.columns),
        "observations": int(len(correlation_frame)),
        "pearson": _matrix(correlation_frame.corr(method="pearson")),
        "spearman": _matrix(correlation_frame.corr(method="spearman")),
    }
    score_correlation = _aggregate_score_correlations(executions)
    metrics = PerformanceMetrics.summarize(strategy, periods_per_year=12)
    thresholds = _research_checks(metrics, executions, research_thresholds or {})
    snapshot = [
        {
            "date": period.to_timestamp("M").strftime("%Y-%m-%d"),
            "strategy": _finite(row.get("strategy")),
            **{name: _finite(row.get(name)) for name in (*FACTOR_NAMES, "rf") if name in aligned},
        }
        for period, row in aligned.iterrows()
    ]
    return {
        "frequency": "monthly",
        "observations": int(len(complete)),
        "coverage": float(len(complete) / len(strategy)) if len(strategy) else 0.0,
        "capm": capm,
        "multi_factor": multi_factor,
        "factor_return_correlation": factor_correlation,
        "selection_score_correlation": score_correlation,
        "research_checks": thresholds,
        "input_snapshot": snapshot,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _monthly_returns(values: pd.Series) -> pd.Series:
    series = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    if series.empty:
        return pd.Series(dtype=float, index=pd.PeriodIndex([], freq="M"))
    series.index = pd.to_datetime(series.index)
    periods = series.index.to_period("M")
    result = series.groupby(periods).apply(lambda group: float((1.0 + group).prod() - 1.0))
    result.index = pd.PeriodIndex(result.index, freq="M")
    return result.sort_index().astype(float)


def _monthly_factors(values: pd.DataFrame) -> pd.DataFrame:
    if values.empty:
        return pd.DataFrame(index=pd.PeriodIndex([], freq="M"))
    frame = values.copy()
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.set_index("date")
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame.loc[frame.index.notna()]
    columns = [name for name in (*FACTOR_NAMES, "rf") if name in frame]
    frame = frame[columns].apply(pd.to_numeric, errors="coerce")
    frame.index = frame.index.to_period("M")
    return frame.groupby(level=0).last().sort_index()


def _regression(frame: pd.DataFrame, factor_names: tuple[str, ...]) -> dict[str, Any]:
    clean = frame.dropna(subset=["strategy", "rf", *factor_names]).copy()
    parameter_count = len(factor_names) + 1
    minimum = max(parameter_count + 2, 6)
    if len(clean) < minimum:
        result = _empty_regression(factor_names)
        result["observations"] = int(len(clean))
        result["warning"] = f"At least {minimum} complete monthly observations are required"
        return result

    y = (clean["strategy"] - clean["rf"]).to_numpy(dtype=float)
    columns = []
    for name in factor_names:
        values = clean[name] - clean["rf"] if name == "MKT" else clean[name]
        columns.append(values.to_numpy(dtype=float))
    x = np.column_stack([np.ones(len(clean)), *columns])
    coefficients, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
    fitted = x @ coefficients
    residuals = y - fitted
    dof = len(y) - x.shape[1]
    covariance = _newey_west_covariance(x, residuals)
    standard_errors = np.sqrt(np.maximum(0.0, np.diag(covariance)))
    t_stats = np.divide(
        coefficients,
        standard_errors,
        out=np.zeros_like(coefficients),
        where=standard_errors > 0,
    )
    critical = float(student_t.ppf(0.975, dof)) if dof > 0 else 1.96
    total = float(np.sum((y - y.mean()) ** 2))
    residual_sum = float(np.sum(residuals**2))
    r_squared = 1.0 - residual_sum / total if total > 0 else 0.0
    alpha = float(coefficients[0])
    names = ("alpha", *factor_names)
    estimates = {
        name: {
            "estimate": float(coefficients[index]),
            "standard_error": float(standard_errors[index]),
            "t_stat": float(t_stats[index]),
            "confidence_95": [
                float(coefficients[index] - critical * standard_errors[index]),
                float(coefficients[index] + critical * standard_errors[index]),
            ],
        }
        for index, name in enumerate(names)
    }
    return {
        "observations": int(len(clean)),
        "alpha_monthly": alpha,
        "alpha_annualized": float((1.0 + alpha) ** 12 - 1.0) if alpha > -1 else -1.0,
        "betas": {name: float(coefficients[index + 1]) for index, name in enumerate(factor_names)},
        "r_squared": float(r_squared),
        "residual_volatility_annualized": float(
            np.std(residuals, ddof=max(1, x.shape[1])) * np.sqrt(12)
        ),
        "estimates": estimates,
        "warning": "Factor matrix is rank deficient" if rank < x.shape[1] else None,
    }


def _newey_west_covariance(x: np.ndarray, residuals: np.ndarray) -> np.ndarray:
    observations, parameters = x.shape
    bread = np.linalg.pinv(x.T @ x)
    scores = x * residuals[:, None]
    lag_count = min(
        observations - 1,
        max(1, int(np.floor(4.0 * (observations / 100.0) ** (2.0 / 9.0)))),
    )
    meat = scores.T @ scores
    for lag in range(1, lag_count + 1):
        weight = 1.0 - lag / (lag_count + 1.0)
        covariance = scores[lag:].T @ scores[:-lag]
        meat += weight * (covariance + covariance.T)
    correction = observations / max(1, observations - parameters)
    return correction * bread @ meat @ bread


def _empty_regression(factor_names: tuple[str, ...]) -> dict[str, Any]:
    return {
        "observations": 0,
        "alpha_monthly": None,
        "alpha_annualized": None,
        "betas": {name: None for name in factor_names},
        "r_squared": None,
        "residual_volatility_annualized": None,
        "estimates": {},
        "warning": None,
    }


def _matrix(values: pd.DataFrame) -> list[list[float | None]]:
    return [
        [_finite(value) for value in values.loc[row, values.columns].tolist()]
        for row in values.index
    ]


def _aggregate_score_correlations(
    executions: list[dict] | tuple[dict, ...],
) -> dict[str, Any]:
    matrices: list[pd.DataFrame] = []
    labels: list[str] = []
    for execution in executions:
        payload = execution.get("factor_score_correlation") if isinstance(execution, dict) else None
        if not isinstance(payload, dict) or not isinstance(payload.get("matrix"), list):
            continue
        current = [str(value) for value in payload.get("labels") or []]
        if not current or len(payload["matrix"]) != len(current):
            continue
        frame = pd.DataFrame(payload["matrix"], index=current, columns=current, dtype=float)
        matrices.append(frame)
        labels = sorted(set(labels) | set(current))
    if not matrices:
        return {"labels": [], "periods": 0, "median_spearman": []}
    cube = np.stack(
        [frame.reindex(index=labels, columns=labels).to_numpy(dtype=float) for frame in matrices]
    )
    with np.errstate(invalid="ignore"):
        median = np.nanmedian(cube, axis=0)
    return {
        "labels": labels,
        "periods": len(matrices),
        "median_spearman": [[_finite(value) for value in row] for row in median.tolist()],
    }


def _research_checks(
    metrics: dict[str, float | int],
    executions: list[dict] | tuple[dict, ...],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    configured = {
        "min_sharpe": float(thresholds.get("min_sharpe", 0.5)),
        "max_drawdown": float(thresholds.get("max_drawdown", -0.35)),
        "max_turnover": float(thresholds.get("max_turnover", 1.0)),
    }
    turnovers = [
        float(item["turnover"])
        for item in executions
        if isinstance(item, dict) and item.get("turnover") is not None
    ]
    average_turnover = float(np.mean(turnovers)) if turnovers else None
    checks = {
        "min_sharpe": float(metrics.get("sharpe", 0.0)) >= configured["min_sharpe"],
        "max_drawdown": float(metrics.get("max_drawdown", 0.0)) >= configured["max_drawdown"],
        "max_turnover": average_turnover is not None
        and average_turnover <= configured["max_turnover"],
    }
    return {
        "passed": all(checks.values()),
        "thresholds": configured,
        "observed": {
            "sharpe": _finite(metrics.get("sharpe")),
            "max_drawdown": _finite(metrics.get("max_drawdown")),
            "average_turnover": _finite(average_turnover),
        },
        "checks": checks,
    }


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


__all__ = ["FACTOR_NAMES", "factor_attribution"]
