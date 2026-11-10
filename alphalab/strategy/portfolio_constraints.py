"""Core-owned verification of portfolio target weights against declared rules."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import Any, Mapping

import pandas as pd

from alphalab.sdk.v1.constraint_math import CovarianceByModel, evaluate_constraints
from alphalab.sdk.v1.constraints import (
    ConstraintCheck,
    PortfolioConstraintData,
    PortfolioConstraintError,
    PortfolioConstraintSet,
)
from alphalab.sdk.v1.model import PortfolioDecision


class PortfolioConstraintViolation(ValueError):
    """A target whose independently recomputed checks do not all pass."""

    def __init__(self, checks: tuple[ConstraintCheck, ...]) -> None:
        self.checks = checks
        failures = [
            f"{check.layer}/{check.constraint_id}/{check.subject}: {check.status} "
            f"(actual={check.actual}, lower={check.lower}, upper={check.upper})"
            for check in checks
            if check.status != "pass"
        ]
        super().__init__("portfolio constraints failed: " + "; ".join(failures))


def verify_portfolio_constraints(
    decision: PortfolioDecision,
    *,
    project_constraints: PortfolioConstraintSet | Mapping[str, Any] | None = None,
    constraint_data: PortfolioConstraintData | Mapping[str, Any] | None = None,
    covariance_by_model: CovarianceByModel | None = None,
    as_of: dt.date | str | pd.Timestamp | None = None,
) -> tuple[ConstraintCheck, ...]:
    """Recompute project and strategy checks using only core-supplied market data.

    A decision may name a data version for audit, but cannot supply its own
    classifications, benchmark weights, or covariance. Missing core inputs
    fail closed rather than accepting the strategy's optimizer diagnostics.
    """

    project_set = _constraint_set(project_constraints)
    layers = (("project", project_set), ("strategy", decision.constraints))
    if not any(rule_set is not None and rule_set.constraints for _, rule_set in layers):
        return ()
    if constraint_data is None:
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_MISSING", "core constraint data is unavailable for target verification"
        )
    data = (
        PortfolioConstraintData.from_dict(constraint_data)
        if isinstance(constraint_data, Mapping)
        else constraint_data
    )
    if not isinstance(data, PortfolioConstraintData):
        raise TypeError("constraint_data must be PortfolioConstraintData")
    if as_of is not None and data.as_of != pd.Timestamp(as_of).date():
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_INVALID", "core constraint data date does not match decision date"
        )
    if (
        decision.constraint_data_version is not None
        and decision.constraint_data_version != data.data_version
    ):
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_VERSION_INVALID",
            "decision data version does not match independently loaded core data",
        )
    checks = tuple(
        check
        for layer, rule_set in layers
        if rule_set is not None and rule_set.constraints
        for check in evaluate_constraints(
            rule_set,
            data,
            decision.target_weights,
            layer=layer,
            stage="target",
            covariance_by_model=covariance_by_model,
        )
    )
    evaluated = {(check.layer, check.constraint_id) for check in checks}
    for layer, rule_set in layers:
        if rule_set is not None:
            for constraint in rule_set.constraints:
                if (layer, constraint.id) not in evaluated:
                    raise PortfolioConstraintError(
                        "CONSTRAINT_DATA_MISSING",
                        f"no target check was produced for {layer} constraint {constraint.id!r}",
                        constraint_id=constraint.id,
                    )
    if any(check.status != "pass" for check in checks):
        raise PortfolioConstraintViolation(checks)
    return checks


def verify_planned_portfolio_constraints(
    decision: PortfolioDecision,
    planned_weights: Mapping[str, float],
    current_weights: Mapping[str, float],
    *,
    project_constraints: PortfolioConstraintSet | Mapping[str, Any] | None = None,
    constraint_data: PortfolioConstraintData | Mapping[str, Any] | None = None,
    covariance_by_model: CovarianceByModel | None = None,
    as_of: dt.date | str | pd.Timestamp | None = None,
) -> tuple[ConstraintCheck, ...]:
    """Check the executable, post-cost plan against the pre-trade book.

    A still-violated check is allowed only when it improves on the actual
    pre-trade book. Both books use the same core-owned execution-date data.
    """

    project_set = _constraint_set(project_constraints)
    layers = (("project", project_set), ("strategy", decision.constraints))
    if not any(rule_set is not None and rule_set.constraints for _, rule_set in layers):
        return ()
    if constraint_data is None:
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_MISSING",
            "core constraint data is unavailable for planned verification",
        )
    data = (
        PortfolioConstraintData.from_dict(constraint_data)
        if isinstance(constraint_data, Mapping)
        else constraint_data
    )
    if not isinstance(data, PortfolioConstraintData):
        raise TypeError("constraint_data must be PortfolioConstraintData")
    if as_of is not None and data.as_of != pd.Timestamp(as_of).date():
        raise PortfolioConstraintError(
            "CONSTRAINT_DATA_INVALID", "core constraint data date does not match execution date"
        )

    # Keep securities that are bought or fully exited in both evaluations.
    # An implicit per-security risk budget must see the same subject universe.
    symbols = sorted(set(current_weights) | set(planned_weights))
    before_weights = {symbol: current_weights.get(symbol, 0.0) for symbol in symbols}
    after_weights = {symbol: planned_weights.get(symbol, 0.0) for symbol in symbols}
    result: list[ConstraintCheck] = []
    for layer, rule_set in layers:
        if rule_set is None or not rule_set.constraints:
            continue
        before = evaluate_constraints(
            rule_set,
            data,
            before_weights,
            layer=layer,
            stage="planned",
            covariance_by_model=covariance_by_model,
        )
        planned = evaluate_constraints(
            rule_set,
            data,
            after_weights,
            layer=layer,
            stage="planned",
            covariance_by_model=covariance_by_model,
        )
        references = {(check.constraint_id, check.subject): check for check in before}
        evaluated = {(check.constraint_id, check.subject) for check in planned}
        for constraint in rule_set.constraints:
            if not any(key[0] == constraint.id for key in evaluated):
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_MISSING",
                    f"no planned check was produced for {layer} constraint {constraint.id!r}",
                    constraint_id=constraint.id,
                )
        for check in planned:
            reference = references.get((check.constraint_id, check.subject))
            if reference is None:
                raise PortfolioConstraintError(
                    "CONSTRAINT_DATA_MISSING",
                    f"no pre-trade check was produced for {layer}/{check.constraint_id}/{check.subject}",
                    constraint_id=check.constraint_id,
                )
            if (
                check.status == "fail"
                and reference.status == "fail"
                and check.slack is not None
                and reference.slack is not None
                and check.slack > reference.slack + check.tolerance
            ):
                check = replace(check, status="improved", reference=reference.actual)
            result.append(check)
    checks = tuple(result)
    if any(check.status not in {"pass", "improved"} for check in checks):
        raise PortfolioConstraintViolation(checks)
    return checks


def _constraint_set(
    value: PortfolioConstraintSet | Mapping[str, Any] | None,
) -> PortfolioConstraintSet | None:
    if isinstance(value, Mapping):
        return PortfolioConstraintSet.from_dict(value)
    if value is not None and not isinstance(value, PortfolioConstraintSet):
        raise TypeError("project_constraints must be PortfolioConstraintSet")
    return value
