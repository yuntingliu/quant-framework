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
from alphalab.strategy.engine import _execute_target
from alphalab.strategy.portfolio_constraints import (
    PortfolioConstraintViolation,
    verify_planned_portfolio_constraints,
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

    checks = verify_portfolio_constraints(
        PortfolioDecision(target_weights={}, constraints=_cap(0.8, groups=None)),
        constraint_data=case["data"],
    )
    assert {check.subject for check in checks} == {"TECH", "FIN"}
    assert all(check.actual == 0.0 and check.status == "pass" for check in checks)


def test_planned_check_allows_only_strict_improvement(three_asset_constraint_case) -> None:
    case = three_asset_constraint_case
    decision = PortfolioDecision(target_weights={"AAA": 0.6, "CCC": 0.4}, constraints=_cap(0.6))
    common = {"constraint_data": case["data"], "as_of": case["as_of"]}

    improved = verify_planned_portfolio_constraints(
        decision, {"AAA": 0.66, "CCC": 0.34}, {"AAA": 0.7, "CCC": 0.3}, **common
    )
    assert len(improved) == 1
    assert improved[0].status == "improved"
    assert improved[0].reference == pytest.approx(0.7)
    assert improved[0].actual == pytest.approx(0.66)

    for current, planned in [
        ({"AAA": 0.7, "CCC": 0.3}, {"AAA": 0.7, "CCC": 0.3}),
        ({"AAA": 0.7, "CCC": 0.3}, {"AAA": 0.75, "CCC": 0.25}),
        ({"AAA": 0.6, "CCC": 0.4}, {"AAA": 0.7, "CCC": 0.3}),
    ]:
        with pytest.raises(PortfolioConstraintViolation):
            verify_planned_portfolio_constraints(decision, planned, current, **common)


def test_planned_check_requires_core_execution_date_data(three_asset_constraint_case) -> None:
    case = three_asset_constraint_case
    decision = PortfolioDecision(target_weights={"AAA": 0.6}, constraints=_cap(0.6))
    with pytest.raises(PortfolioConstraintError, match="core constraint data is unavailable"):
        verify_planned_portfolio_constraints(decision, {"AAA": 0.6}, {}, as_of=case["as_of"])
    with pytest.raises(PortfolioConstraintError, match="execution date"):
        verify_planned_portfolio_constraints(
            decision,
            {"AAA": 0.6},
            {},
            constraint_data=case["data"],
            as_of=dt.date(2026, 7, 1),
        )


def test_planned_risk_budget_keeps_exited_and_new_symbols(three_asset_constraint_case) -> None:
    case = three_asset_constraint_case
    model = RiskModelSpec(lookback=60, minimum_observations=40)
    risk_set = PortfolioConstraintSet(
        constraints=(
            RiskBudgetConstraint(
                id="each_risk", classification=None, groups=None, upper=0.8, risk_model=model
            ),
        )
    )
    covariance = pd.DataFrame(
        [[1.0, 0.0], [0.0, 1.0]], index=["AAA", "CCC"], columns=["AAA", "CCC"]
    )
    with pytest.raises(PortfolioConstraintViolation) as excinfo:
        verify_planned_portfolio_constraints(
            PortfolioDecision(target_weights={"CCC": 0.5}, constraints=risk_set),
            {"CCC": 0.5},
            {"AAA": 0.5},
            constraint_data=case["data"],
            covariance_by_model={model: covariance},
            as_of=case["as_of"],
        )
    assert [(check.subject, check.status) for check in excinfo.value.checks] == [
        ("AAA", "pass"),
        ("CCC", "fail"),
    ]


def test_execution_blocks_a_plan_that_cannot_reduce_existing_violation(
    three_asset_constraint_case,
) -> None:
    case = three_asset_constraint_case
    date = pd.Timestamp(case["as_of"])
    rows = pd.DataFrame(
        [
            {
                "date": date,
                "symbol": "AAA",
                "open": 10.0,
                "close": 10.0,
                "volume": 0.0,
                "amount": 0.0,
            },
            {
                "date": date,
                "symbol": "CCC",
                "open": 10.0,
                "close": 10.0,
                "volume": 1_000_000.0,
                "amount": 10_000_000.0,
            },
        ]
    )
    holdings = {"AAA": 700_000.0, "CCC": 300_000.0}
    pending = {
        "decision": {"target_weights": {"AAA": 0.6, "CCC": 0.4}},
        "policy": {"max_participation_rate": 1.0},
    }
    with pytest.raises(PortfolioConstraintViolation):
        _execute_target(
            pending,
            holdings,
            0.0,
            {},
            rows,
            1_000_000.0,
            field="open",
            project={"settings": {"portfolio_constraints": _cap(0.6).to_dict()}},
            strict_execution_data=False,
            session=None,
            as_of=date,
            event="session_open",
            execution_data_fill=False,
            constraint_data=case["data"].to_dict(),
        )
    assert holdings == {"AAA": 700_000.0, "CCC": 300_000.0}

    # A decision cannot supply the core's dated classification snapshot.
    with pytest.raises(PortfolioConstraintError, match="core constraint data is unavailable"):
        _execute_target(
            {**pending, "constraint_data": case["data"].to_dict()},
            holdings,
            0.0,
            {},
            rows,
            1_000_000.0,
            field="open",
            project={"settings": {"portfolio_constraints": _cap(0.6).to_dict()}},
            strict_execution_data=False,
            session=None,
            as_of=date,
            event="session_open",
            execution_data_fill=False,
        )


def test_execution_books_a_plan_that_improves_existing_violation(
    three_asset_constraint_case,
) -> None:
    case = three_asset_constraint_case
    date = pd.Timestamp(case["as_of"])
    rows = pd.DataFrame(
        [
            {
                "date": date,
                "symbol": "AAA",
                "open": 10.0,
                "close": 10.0,
                "volume": 1_000_000.0,
                "amount": 40_000.0,
            },
            {
                "date": date,
                "symbol": "CCC",
                "open": 10.0,
                "close": 10.0,
                "volume": 1_000_000.0,
                "amount": 10_000_000.0,
            },
        ]
    )
    values, cash, _, audit = _execute_target(
        {
            "decision": {"target_weights": {"AAA": 0.6, "CCC": 0.4}},
            "policy": {"max_participation_rate": 1.0},
        },
        {"AAA": 700_000.0, "CCC": 300_000.0},
        0.0,
        {},
        rows,
        1_000_000.0,
        field="open",
        project={"settings": {"portfolio_constraints": _cap(0.6).to_dict()}},
        strict_execution_data=False,
        session=None,
        as_of=date,
        event="session_open",
        execution_data_fill=False,
        constraint_data=case["data"].to_dict(),
    )
    assert values["AAA"] == pytest.approx(660_000.0)
    assert cash == pytest.approx(0.0, abs=1e-8)
    assert audit["state_committed"] is True


def test_execution_rechecks_weights_after_transaction_costs(three_asset_constraint_case) -> None:
    case = three_asset_constraint_case
    date = pd.Timestamp(case["as_of"])
    rows = pd.DataFrame(
        [
            {
                "date": date,
                "symbol": "AAA",
                "open": 10.0,
                "close": 10.0,
                "volume": 1_000_000.0,
                "amount": 10_000_000.0,
            }
        ]
    )
    with pytest.raises(PortfolioConstraintViolation):
        _execute_target(
            {
                "decision": {"target_weights": {"AAA": 0.6}},
                "policy": {"max_participation_rate": 1.0, "commission_rate": 0.01},
            },
            {"AAA": 500_000.0},
            500_000.0,
            {},
            rows,
            1_000_000.0,
            field="open",
            project={"settings": {"portfolio_constraints": _cap(0.6).to_dict()}},
            strict_execution_data=False,
            session=None,
            as_of=date,
            event="session_open",
            execution_data_fill=False,
            constraint_data=case["data"].to_dict(),
        )
