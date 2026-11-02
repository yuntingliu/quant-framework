"""Deterministic portfolio-constraint calculations shared by SDK and core.

This module owns mathematical evaluation only.  It does not load provider data,
repair covariance matrices, relax constraints, or invoke an optimizer.  Keeping
that boundary small lets the optimizer and the pre-execution verifier recompute
the same checks from independently sourced inputs.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from alphalab.sdk.v1.constraints import (
    CHECK_LAYERS,
    CHECK_STAGES,
    BenchmarkDeviationConstraint,
    ConstraintCheck,
    GroupWeightConstraint,
    PortfolioConstraintData,
    PortfolioConstraintError,
    PortfolioConstraintSet,
    RiskBudgetConstraint,
    RiskModelSpec,
    validate_constraint_data,
)

RISK_VARIANCE_FLOOR = 1e-20

CovarianceByModel = Mapping[RiskModelSpec, pd.DataFrame]


def group_weights(
    weights: Mapping[str, float],
    group_by_symbol: Mapping[str, str],
    *,
    subjects: Iterable[str] | None = None,
) -> dict[str, float]:
    """Aggregate raw risky-asset weights by group without rescaling cash.

    ``group_by_symbol`` must cover every non-zero symbol.  Passing ``subjects``
    also emits explicitly constrained groups that currently have zero weight.
    """

    normalized = _weights(weights)
    groups = _normalized_group_map(group_by_symbol)
    missing = sorted(symbol for symbol, weight in normalized.items() if weight and symbol not in groups)
    if missing:
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_MISSING",
            f"group mapping is missing weighted symbols: {missing}",
            subject=missing[0],
        )
    selected = _subject_names(subjects) if subjects is not None else None
    totals: dict[str, float] = {subject: 0.0 for subject in selected or ()}
    for symbol, weight in normalized.items():
        if not weight:
            continue
        group = groups[symbol]
        if selected is None or group in totals:
            totals[group] = totals.get(group, 0.0) + weight
    return {subject: float(totals[subject]) for subject in sorted(totals)}


def benchmark_deviations(
    weights: Mapping[str, float],
    benchmark_weights: Mapping[str, float],
    *,
    group_by_symbol: Mapping[str, str] | None = None,
    subjects: Iterable[str] | None = None,
) -> dict[str, float]:
    """Return raw portfolio-minus-benchmark weight by security or group.

    Cash is deliberately absent from both maps and is not redistributed.  A
    fully invested benchmark therefore exposes portfolio cash as underweight.
    """

    portfolio = _weights(weights)
    benchmark = _weights(benchmark_weights, label="benchmark weights", require_unit_sum=True)
    selected = _subject_names(subjects, symbols=group_by_symbol is None) if subjects is not None else None
    if group_by_symbol is None:
        names = set(portfolio) | set(benchmark) if selected is None else set(selected)
        return {
            symbol: float(portfolio.get(symbol, 0.0) - benchmark.get(symbol, 0.0))
            for symbol in sorted(names)
        }

    groups = _normalized_group_map(group_by_symbol)
    needed = {
        symbol
        for symbol, weight in {**benchmark, **portfolio}.items()
        if portfolio.get(symbol, 0.0) != 0.0 or benchmark.get(symbol, 0.0) != 0.0
    }
    missing = sorted(needed - set(groups))
    if missing:
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_MISSING",
            f"group mapping is missing portfolio or benchmark symbols: {missing}",
            subject=missing[0],
        )
    names = set(groups[symbol] for symbol in needed) if selected is None else set(selected)
    deviations = {name: 0.0 for name in names}
    for symbol in needed:
        group = groups[symbol]
        if group in deviations:
            deviations[group] += portfolio.get(symbol, 0.0) - benchmark.get(symbol, 0.0)
    return {subject: float(deviations[subject]) for subject in sorted(deviations)}


def risk_contributions(
    weights: Mapping[str, float], covariance: pd.DataFrame
) -> dict[str, float] | None:
    """Return each security's share of portfolio variance.

    The covariance must be a labelled square frame.  Labels prevent accidental
    positional misalignment between optimizer and verifier.  ``None`` denotes
    undefined contributions when total variance is at or below the contract's
    numerical floor.
    """

    portfolio = _weights(weights)
    frame = _covariance_frame(covariance)
    weighted = sorted(symbol for symbol, weight in portfolio.items() if weight != 0.0)
    missing = sorted(set(weighted) - set(frame.index))
    if missing:
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_MISSING",
            f"covariance is missing weighted symbols: {missing}",
            subject=missing[0],
        )
    vector = np.array([portfolio.get(symbol, 0.0) for symbol in frame.index], dtype=float)
    matrix = frame.to_numpy(dtype=float)
    variance = float(vector @ matrix @ vector)
    if variance <= RISK_VARIANCE_FLOOR:
        return None
    values = vector * (matrix @ vector) / variance
    return {symbol: float(value) for symbol, value in zip(frame.index, values)}


def evaluate_constraints(
    constraint_set: PortfolioConstraintSet,
    data: PortfolioConstraintData,
    weights: Mapping[str, float],
    *,
    layer: str = "strategy",
    stage: str = "optimizer",
    covariance_by_model: CovarianceByModel | None = None,
) -> tuple[ConstraintCheck, ...]:
    """Evaluate every declared hard constraint in deterministic order.

    Risk covariances are keyed by ``RiskModelSpec`` rather than supplied as one
    ambient matrix.  This makes multiple lookbacks unambiguous and lets the
    optimizer and core independently rebuild exactly the model named by each
    constraint.  The function returns checks even when risk is mathematically
    undefined; missing required inputs remain hard data errors.
    """

    if not isinstance(constraint_set, PortfolioConstraintSet):
        raise TypeError("constraint_set must be a PortfolioConstraintSet")
    if not isinstance(data, PortfolioConstraintData):
        raise TypeError("data must be PortfolioConstraintData")
    if layer not in CHECK_LAYERS:
        raise ValueError(f"layer must be one of {sorted(CHECK_LAYERS)}")
    if stage not in CHECK_STAGES:
        raise ValueError(f"stage must be one of {sorted(CHECK_STAGES)}")

    portfolio = _weights(weights)
    nonzero_symbols = [symbol for symbol, weight in portfolio.items() if weight != 0.0]
    validate_constraint_data(constraint_set, data, nonzero_symbols)
    covariances = covariance_by_model or {}
    checks: list[ConstraintCheck] = []
    common = {
        "layer": layer,
        "stage": stage,
        "tolerance": constraint_set.tolerance,
        "constraint_set_sha256": constraint_set.constraint_set_sha256,
        "data_version": data.data_version,
    }

    for constraint in constraint_set.constraints:
        if isinstance(constraint, GroupWeightConstraint):
            group_map = data.group_map(
                constraint.classification,
                nonzero_symbols,
                missing_policy=constraint.missing_policy,
                constraint_id=constraint.id,
            )
            actuals = group_weights(portfolio, group_map, subjects=constraint.groups)
            checks.extend(
                _bounded_checks(
                    actuals,
                    constraint=constraint,
                    lower=constraint.lower,
                    upper=constraint.upper,
                    common=common,
                )
            )
            continue

        if isinstance(constraint, BenchmarkDeviationConstraint):
            benchmark = data.benchmarks[constraint.benchmark]
            if constraint.classification is None:
                actuals = benchmark_deviations(
                    portfolio, benchmark.weights, subjects=constraint.groups
                )
            else:
                relevant = set(nonzero_symbols)
                relevant.update(symbol for symbol, weight in benchmark.weights.items() if weight > 0)
                group_map = data.group_map(
                    constraint.classification,
                    relevant,
                    missing_policy=constraint.missing_policy,
                    constraint_id=constraint.id,
                )
                actuals = benchmark_deviations(
                    portfolio,
                    benchmark.weights,
                    group_by_symbol=group_map,
                    subjects=constraint.groups,
                )
            limit = constraint.max_abs_deviation
            checks.extend(
                _bounded_checks(
                    actuals,
                    constraint=constraint,
                    lower=-limit,
                    upper=limit,
                    common=common,
                )
            )
            continue

        if isinstance(constraint, RiskBudgetConstraint):
            covariance = covariances.get(constraint.risk_model)
            if covariance is None:
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_MISSING",
                    f"risk covariance is missing for constraint {constraint.id!r}",
                    constraint_id=constraint.id,
                )
            security_contributions = risk_contributions(portfolio, covariance)
            subjects, group_map = _risk_subjects(constraint, data, nonzero_symbols)
            if security_contributions is None:
                checks.extend(
                    _undefined_risk_checks(
                        subjects, constraint=constraint, common=common
                    )
                )
                continue
            if group_map is None:
                actuals = {
                    symbol: security_contributions.get(symbol, 0.0) for symbol in subjects
                }
            else:
                actuals = {subject: 0.0 for subject in subjects}
                for symbol, contribution in security_contributions.items():
                    group = group_map.get(symbol)
                    if group in actuals:
                        actuals[group] += contribution
            checks.extend(
                _bounded_checks(
                    actuals,
                    constraint=constraint,
                    lower=None,
                    upper=constraint.upper,
                    common=common,
                )
            )

    return tuple(checks)


def _risk_subjects(
    constraint: RiskBudgetConstraint,
    data: PortfolioConstraintData,
    symbols: Sequence[str],
) -> tuple[list[str], dict[str, str] | None]:
    if constraint.classification is None:
        subjects = list(constraint.groups) if constraint.groups is not None else list(symbols)
        return sorted(subjects), None
    group_map = data.group_map(
        constraint.classification,
        symbols,
        missing_policy=constraint.missing_policy,
        constraint_id=constraint.id,
    )
    subjects = (
        list(constraint.groups)
        if constraint.groups is not None
        else sorted(set(group_map.values()))
    )
    return sorted(subjects), group_map


def _bounded_checks(
    actuals: Mapping[str, float],
    *,
    constraint: GroupWeightConstraint | BenchmarkDeviationConstraint | RiskBudgetConstraint,
    lower: float | None,
    upper: float | None,
    common: Mapping[str, object],
) -> list[ConstraintCheck]:
    return [
        ConstraintCheck.from_bounds(
            constraint_id=constraint.id,
            kind=constraint.kind,
            subject=subject,
            actual=float(actuals[subject]),
            lower=lower,
            upper=upper,
            **common,
        )
        for subject in sorted(actuals)
    ]


def _undefined_risk_checks(
    subjects: Sequence[str],
    *,
    constraint: RiskBudgetConstraint,
    common: Mapping[str, object],
) -> list[ConstraintCheck]:
    return [
        ConstraintCheck(
            constraint_id=constraint.id,
            kind=constraint.kind,
            subject=subject,
            actual=None,
            reference=None,
            lower=None,
            upper=constraint.upper,
            slack=None,
            status="not_evaluated",
            reason_code="RISK_UNDEFINED",
            message="portfolio variance is too small to define risk contributions",
            **common,
        )
        for subject in sorted(subjects)
    ]


def _weights(
    values: Mapping[str, float],
    *,
    label: str = "weights",
    require_unit_sum: bool = False,
) -> dict[str, float]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{label} must be a mapping")
    normalized: dict[str, float] = {}
    for raw_symbol, raw_value in values.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol or symbol in normalized:
            raise ValueError(f"{label} symbols must normalize to unique non-empty values")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(f"{label} for {symbol} must be numeric")
        value = float(raw_value)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{label} for {symbol} must be finite and non-negative")
        normalized[symbol] = value
    if require_unit_sum and abs(math.fsum(normalized.values()) - 1.0) > 1e-3:
        raise ValueError(f"{label} must sum to 1 +/- 0.001")
    return normalized


def _normalized_group_map(values: Mapping[str, str]) -> dict[str, str]:
    groups: dict[str, str] = {}
    for raw_symbol, raw_group in values.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol or symbol in groups:
            raise ValueError("group-map symbols must normalize to unique non-empty values")
        if not isinstance(raw_group, str) or not raw_group.strip():
            raise ValueError(f"group for {symbol} must be a non-empty string")
        groups[symbol] = raw_group.strip()
    return groups


def _subject_names(values: Iterable[str], *, symbols: bool = False) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("constraint subjects must be non-empty strings")
        subject = value.strip().upper() if symbols else value.strip()
        if subject in seen:
            raise ValueError("constraint subjects must not repeat")
        seen.add(subject)
        result.append(subject)
    return result


def _covariance_frame(value: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame):
        raise TypeError("covariance must be a labelled pandas DataFrame")
    frame = value.copy()
    if frame.empty or frame.shape[0] != frame.shape[1]:
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_INVALID", "covariance must be a non-empty square matrix"
        )
    index = [str(symbol).strip().upper() for symbol in frame.index]
    columns = [str(symbol).strip().upper() for symbol in frame.columns]
    if (
        not all(index)
        or not all(columns)
        or len(set(index)) != len(index)
        or len(set(columns)) != len(columns)
        or set(index) != set(columns)
    ):
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_INVALID",
            "covariance row and column labels must be unique matching symbols",
        )
    frame.index = index
    frame.columns = columns
    frame = frame.loc[index, index].apply(pd.to_numeric, errors="coerce")
    matrix = frame.to_numpy(dtype=float)
    if not np.isfinite(matrix).all():
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_INVALID", "covariance contains non-finite values"
        )
    if not np.allclose(matrix, matrix.T, rtol=1e-10, atol=1e-12):
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_INVALID", "covariance must be symmetric"
        )
    scale = max(float(np.max(np.abs(matrix))), 1.0)
    minimum_eigenvalue = float(np.linalg.eigvalsh(matrix).min())
    if minimum_eigenvalue < -max(scale * 1e-10, 1e-12):
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_INVALID",
            "covariance must be positive semidefinite or repaired before evaluation",
        )
    return frame


__all__ = [
    "RISK_VARIANCE_FLOOR",
    "benchmark_deviations",
    "evaluate_constraints",
    "group_weights",
    "risk_contributions",
]
