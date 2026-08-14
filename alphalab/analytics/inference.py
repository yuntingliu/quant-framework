"""Deterministic time-series inference helpers for research diagnostics."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def moving_block_mean_interval(
    values: pd.Series,
    *,
    draws: int,
    minimum_observations: int,
) -> dict[str, float | None]:
    """Return a circular moving-block bootstrap interval for the mean."""

    clean = _array(values)
    if len(clean) < minimum_observations:
        return {"lower": None, "upper": None}
    block_length = min(len(clean), max(2, int(round(len(clean) ** (1.0 / 3.0)))))
    blocks_per_draw = int(math.ceil(len(clean) / block_length))
    rng = np.random.default_rng(0)
    starts = rng.integers(0, len(clean), size=(draws, blocks_per_draw))
    offsets = np.arange(block_length)
    indices = (starts[:, :, None] + offsets[None, None, :]) % len(clean)
    samples = clean[indices].reshape(draws, -1)[:, : len(clean)]
    lower, upper = np.quantile(samples.mean(axis=1), [0.025, 0.975])
    return {"lower": float(lower), "upper": float(upper)}


def newey_west_mean_t_stat(values: pd.Series) -> float:
    """Test a zero mean with a Bartlett-kernel HAC standard error."""

    clean = _array(values)
    if len(clean) < 2:
        return 0.0
    mean = float(np.mean(clean))
    demeaned = clean - mean
    lag_count = min(
        len(clean) - 1,
        max(1, int(math.floor(4.0 * (len(clean) / 100.0) ** (2.0 / 9.0)))),
    )
    long_run_variance = float(np.dot(demeaned, demeaned) / len(clean))
    for lag in range(1, lag_count + 1):
        covariance = float(
            np.dot(demeaned[lag:], demeaned[:-lag]) / len(clean)
        )
        long_run_variance += 2.0 * (1.0 - lag / (lag_count + 1.0)) * covariance
    variance_of_mean = max(0.0, long_run_variance / len(clean))
    if variance_of_mean <= 0:
        return 0.0
    return float(mean / math.sqrt(variance_of_mean))


def newey_west_one_sided_p_value(values: pd.Series) -> float:
    statistic = newey_west_mean_t_stat(values)
    if not np.isfinite(statistic):
        return 0.0 if statistic > 0 else 1.0
    return float(0.5 * math.erfc(statistic / math.sqrt(2.0)))


def _array(values: pd.Series) -> np.ndarray:
    return (
        pd.to_numeric(values, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .to_numpy(dtype=float)
    )


__all__ = [
    "moving_block_mean_interval",
    "newey_west_mean_t_stat",
    "newey_west_one_sided_p_value",
]
