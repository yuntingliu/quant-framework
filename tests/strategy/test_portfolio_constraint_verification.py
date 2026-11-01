"""Core target verification uses independently supplied constraint inputs."""

import datetime as dt

import pandas as pd
import pytest

from alphalab.sdk.v1 import (
    GroupWeightConstraint,
    PortfolioConstraintError,
    PortfolioConstraintSet,
    PortfolioDecision,
    RiskBudgetConstraint,
    RiskModelSpec,
)
from alphalab.strategy.portfolio_constraints import (
    PortfolioConstraintViolation,
    verify_portfolio_constraints,
)
from alphalab.strategy.sdk_runtime import SdkRuntimeError, _validate_decision


def _cap(upper: float, *, groups=("TECH",)) -> PortfolioConstraintSet:
    return PortfolioConstraintSet(
        constraints=(
            GroupWeightConstraint(
                id="technology_cap", classification="test_scheme:1", groups=groups, upper=upper
            ),
        )
    )


def test_project_and_strategy_layers_are_checked_independently(three_asset_constraint_case) -> None:
    case = three_asset_constraint_case
    decision = PortfolioDecision(
        target_weights=case["weights"],
        constraints=_cap(0.6),
        constraint_data_version=case["data"].data_version,
        diagnostics={"claimed_passed": True},
    )

    with pytest.raises(PortfolioConstraintViolation) as excinfo:
        verify_portfolio_constraints(
            decision,
            project_constraints=_cap(0.8).to_dict(),
            constraint_data=case["data"].to_dict(),
            as_of=case["as_of"],
        )
    assert [(check.layer, check.stage, check.status) for check in excinfo.value.checks] == [
        ("project", "target", "pass"),
        ("strategy", "target", "fail"),
    ]


def test_project_rule_is_enforced_without_strategy_rule(three_asset_constraint_case) -> None:
    case = three_asset_constraint_case
    decision = PortfolioDecision(target_weights=case["weights"])
    checks = verify_portfolio_constraints(
        decision, project_constraints=_cap(0.8), constraint_data=case["data"]
    )
    assert len(checks) == 1
    assert checks[0].layer == "project" and checks[0].status == "pass"

    with pytest.raises(SdkRuntimeError, match="project/technology_cap/TECH"):
        _validate_decision(
            decision,
            case["symbols"],
            {
                "as_of": case["as_of"],
                "limits": {"portfolio_constraints": _cap(0.6).to_dict()},
                "constraint_data": case["data"].to_dict(),
            },
        )


def test_target_check_fails_closed_without_core_data_or_matching_version(
    three_asset_constraint_case,
) -> None:
    case = three_asset_constraint_case
    decision = PortfolioDecision(target_weights=case["weights"], constraints=_cap(0.8))
    with pytest.raises(PortfolioConstraintError) as excinfo:
        verify_portfolio_constraints(decision)
    assert excinfo.value.code == "CONSTRAINT_DATA_MISSING"

    wrong_version = PortfolioDecision(
        target_weights=case["weights"],
        constraints=_cap(0.8),
        constraint_data_version="0" * 64,
    )
    with pytest.raises(PortfolioConstraintError) as excinfo:
        verify_portfolio_constraints(wrong_version, constraint_data=case["data"])
    assert excinfo.value.code == "CONSTRAINT_DATA_VERSION_INVALID"

    with pytest.raises(PortfolioConstraintError) as excinfo:
        verify_portfolio_constraints(
            decision,
            constraint_data=case["data"],
            as_of=dt.date(2026, 7, 1),
        )
    assert excinfo.value.code == "CONSTRAINT_DATA_INVALID"


def test_undefined_risk_and_missing_subject_checks_cannot_pass(three_asset_constraint_case) -> None:
    case = three_asset_constraint_case
    model = RiskModelSpec(lookback=60, minimum_observations=40)
    risk_set = PortfolioConstraintSet(
        constraints=(
            RiskBudgetConstraint(
                id="risk_cap", classification=None, groups=("AAA",), upper=0.8, risk_model=model
            ),
        )
    )
    zero_covariance = pd.DataFrame(0.0, index=case["symbols"], columns=case["symbols"])
    with pytest.raises(PortfolioConstraintViolation) as excinfo:
        verify_portfolio_constraints(
            PortfolioDecision(target_weights=case["weights"], constraints=risk_set),
            constraint_data=case["data"],
            covariance_by_model={model: zero_covariance},
        )
    assert excinfo.value.checks[0].status == "not_evaluated"

    with pytest.raises(PortfolioConstraintError) as excinfo:
        verify_portfolio_constraints(
            PortfolioDecision(target_weights={}, constraints=_cap(0.8, groups=None)),
            constraint_data=case["data"],
        )
    assert excinfo.value.code == "CONSTRAINT_DATA_MISSING"
