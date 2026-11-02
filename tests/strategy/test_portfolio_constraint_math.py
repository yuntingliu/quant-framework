from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from alphalab.sdk.v1 import (
    BenchmarkDeviationConstraint,
    GroupWeightConstraint,
    PortfolioConstraintData,
    PortfolioConstraintError,
    PortfolioConstraintSet,
    RiskBudgetConstraint,
    RiskModelSpec,
    evaluate_constraints,
)
from alphalab.sdk.v1.constraint_math import (
    benchmark_deviations,
    group_weights,
    risk_contributions,
)
from alphalab.sdk.v1.constraints import BenchmarkSnapshot, ClassificationSnapshot


def _covariance(case) -> pd.DataFrame:
    return pd.DataFrame(
        case["annual_covariance"], index=case["symbols"], columns=case["symbols"]
    )


def _risk_models(constraint_set: PortfolioConstraintSet, covariance: pd.DataFrame):
    return {
        constraint.risk_model: covariance
        for constraint in constraint_set.constraints
        if isinstance(constraint, RiskBudgetConstraint)
    }


def test_shared_worked_example_evaluates_all_three_constraint_kinds(
    three_asset_constraint_case,
) -> None:
    case = three_asset_constraint_case
    checks = evaluate_constraints(
        case["constraint_set"],
        case["data"],
        case["weights"],
        covariance_by_model=_risk_models(case["constraint_set"], _covariance(case)),
    )

    assert [(check.constraint_id, check.subject) for check in checks] == [
        ("tech_cap", "TECH"),
        ("industry_active", "FIN"),
        ("industry_active", "TECH"),
        ("tech_risk", "TECH"),
    ]
    by_key = {(check.constraint_id, check.subject): check for check in checks}
    assert by_key["tech_cap", "TECH"].actual == pytest.approx(0.70)
    assert by_key["tech_cap", "TECH"].slack == pytest.approx(-0.10)
    assert by_key["industry_active", "TECH"].actual == pytest.approx(0.20)
    assert by_key["industry_active", "FIN"].actual == pytest.approx(-0.25)
    assert by_key["tech_risk", "TECH"].actual == pytest.approx(0.9118, abs=5e-5)
    assert all(check.status == "fail" for check in checks)
    assert all(check.layer == "strategy" and check.stage == "optimizer" for check in checks)
    assert all(
        check.constraint_set_sha256 == case["constraint_set"].constraint_set_sha256
        and check.data_version == case["data"].data_version
        for check in checks
    )


def test_group_weights_use_raw_weights_and_emit_explicit_empty_groups() -> None:
    result = group_weights(
        {"aaa": 0.40, "BBB": 0.30, "CCC": 0.25},
        {"AAA": "TECH", "bbb": "TECH", "CCC": "FIN"},
        subjects=("TECH", "FIN", "ENERGY"),
    )
    assert result == pytest.approx({"ENERGY": 0.0, "FIN": 0.25, "TECH": 0.70})
    assert sum(result.values()) == pytest.approx(0.95)


def test_group_weights_reject_missing_weighted_symbol_but_ignore_zero_weight() -> None:
    assert group_weights({"AAA": 0.5, "MISSING": 0.0}, {"AAA": "A"}) == {"A": 0.5}
    with pytest.raises(PortfolioConstraintError) as excinfo:
        group_weights({"AAA": 0.5, "MISSING": 0.1}, {"AAA": "A"})
    assert (excinfo.value.code, excinfo.value.subject) == (
        "CONSTRAINT_DATA_MISSING",
        "MISSING",
    )


def test_security_benchmark_deviation_includes_members_outside_portfolio() -> None:
    result = benchmark_deviations(
        {"AAA": 0.40, "BBB": 0.30},
        {"AAA": 0.20, "BBB": 0.30, "CCC": 0.50},
    )
    assert result == pytest.approx({"AAA": 0.20, "BBB": 0.0, "CCC": -0.50})
    # The active weights sum to -cash because benchmark weight is not rescaled.
    assert sum(result.values()) == pytest.approx(-0.30)


def test_group_benchmark_deviation_uses_benchmark_only_members() -> None:
    result = benchmark_deviations(
        {"AAA": 0.40},
        {"AAA": 0.20, "BBB": 0.30, "CCC": 0.50},
        group_by_symbol={"AAA": "TECH", "BBB": "TECH", "CCC": "FIN"},
    )
    assert result == pytest.approx({"FIN": -0.50, "TECH": -0.10})


def test_risk_contributions_are_label_aligned_and_may_be_negative() -> None:
    covariance = pd.DataFrame(
        [[0.04, -0.03], [-0.03, 0.09]],
        index=["BBB", "AAA"],
        columns=["BBB", "AAA"],
    )
    result = risk_contributions({"AAA": 0.7, "BBB": 0.3}, covariance)
    assert result is not None
    assert sum(result.values()) == pytest.approx(1.0)
    assert result["BBB"] < 0
    assert result["AAA"] > 1


def test_risk_contributions_return_none_for_zero_variance() -> None:
    covariance = pd.DataFrame(np.zeros((2, 2)), index=["A", "B"], columns=["A", "B"])
    assert risk_contributions({"A": 0.5, "B": 0.5}, covariance) is None


@pytest.mark.parametrize(
    "covariance",
    [
        pd.DataFrame([[1.0, 0.0]], index=["A"], columns=["A", "B"]),
        pd.DataFrame([[1.0, 0.2], [0.1, 1.0]], index=["A", "B"], columns=["A", "B"]),
        pd.DataFrame([[1.0, 2.0], [2.0, 1.0]], index=["A", "B"], columns=["A", "B"]),
        pd.DataFrame([[1.0, np.nan], [np.nan, 1.0]], index=["A", "B"], columns=["A", "B"]),
        pd.DataFrame([[1.0]], index=["A"], columns=["B"]),
    ],
)
def test_risk_contributions_reject_malformed_covariance(covariance) -> None:
    with pytest.raises(PortfolioConstraintError) as excinfo:
        risk_contributions({"A": 1.0}, covariance)
    assert excinfo.value.code == "CONSTRAINT_DATA_INVALID"


def test_risk_contributions_report_missing_weighted_symbols() -> None:
    covariance = pd.DataFrame([[0.1]], index=["A"], columns=["A"])
    with pytest.raises(PortfolioConstraintError) as excinfo:
        risk_contributions({"A": 0.5, "B": 0.5}, covariance)
    assert (excinfo.value.code, excinfo.value.subject) == (
        "CONSTRAINT_DATA_MISSING",
        "B",
    )


def test_explicit_constraints_check_absent_subjects_at_zero(
    three_asset_constraint_case,
) -> None:
    case = three_asset_constraint_case
    constraint_set = PortfolioConstraintSet(
        constraints=(
            GroupWeightConstraint(
                id="energy_floor",
                classification="test_scheme:1",
                groups=("ENERGY",),
                lower=0.10,
            ),
        )
    )
    check = evaluate_constraints(constraint_set, case["data"], case["weights"])[0]
    assert (check.subject, check.actual, check.slack, check.status) == (
        "ENERGY",
        0.0,
        -0.1,
        "fail",
    )


def test_security_level_constraints_normalize_symbols(three_asset_constraint_case) -> None:
    case = three_asset_constraint_case
    constraint_set = PortfolioConstraintSet(
        constraints=(
            BenchmarkDeviationConstraint(
                id="single_name_active",
                benchmark="IDX",
                classification=None,
                groups=("aaa",),
                max_abs_deviation=0.25,
                max_staleness_days=31,
            ),
        )
    )
    check = evaluate_constraints(constraint_set, case["data"], {"aaa": 0.40})[0]
    assert (check.subject, check.actual, check.status) == ("AAA", pytest.approx(0.20), "pass")


def test_unclassified_group_policy_is_applied_during_evaluation() -> None:
    as_of = dt.date(2026, 6, 30)
    data = PortfolioConstraintData(
        as_of=as_of,
        classifications={
            "scheme:1": ClassificationSnapshot(
                classification="scheme:1",
                effective_date=as_of,
                groups={"AAA": "KNOWN"},
                source_version="v1",
            )
        },
        benchmarks={},
    )
    constraint_set = PortfolioConstraintSet(
        constraints=(
            GroupWeightConstraint(
                id="unknown_cap",
                classification="scheme:1",
                groups=("__unclassified__",),
                upper=0.10,
                missing_policy="unclassified_group",
            ),
        )
    )
    check = evaluate_constraints(constraint_set, data, {"AAA": 0.5, "BBB": 0.2})[0]
    assert (check.subject, check.actual, check.status) == (
        "__unclassified__",
        pytest.approx(0.2),
        "fail",
    )


def test_risk_covariances_are_routed_by_model_not_ambient_state(
    three_asset_constraint_case,
) -> None:
    case = three_asset_constraint_case
    short = RiskModelSpec(lookback=20, minimum_observations=15)
    long = RiskModelSpec(lookback=60, minimum_observations=40)
    constraint_set = PortfolioConstraintSet(
        constraints=(
            RiskBudgetConstraint(
                id="short_risk",
                classification=None,
                groups=("AAA",),
                upper=0.5,
                risk_model=short,
            ),
            RiskBudgetConstraint(
                id="long_risk",
                classification=None,
                groups=("AAA",),
                upper=0.5,
                risk_model=long,
            ),
        )
    )
    covariance = _covariance(case)
    diagonal = pd.DataFrame(np.diag(np.diag(covariance)), index=covariance.index, columns=covariance.columns)
    checks = evaluate_constraints(
        constraint_set,
        case["data"],
        case["weights"],
        covariance_by_model={short: diagonal, long: covariance},
    )
    assert checks[0].actual != pytest.approx(checks[1].actual)


def test_missing_risk_covariance_is_a_structured_data_error(
    three_asset_constraint_case,
) -> None:
    case = three_asset_constraint_case
    with pytest.raises(PortfolioConstraintError) as excinfo:
        evaluate_constraints(case["constraint_set"], case["data"], case["weights"])
    assert (excinfo.value.code, excinfo.value.constraint_id) == (
        "CONSTRAINT_DATA_MISSING",
        "tech_risk",
    )


def test_undefined_risk_produces_auditable_checks(three_asset_constraint_case) -> None:
    case = three_asset_constraint_case
    constraint = next(
        item
        for item in case["constraint_set"].constraints
        if isinstance(item, RiskBudgetConstraint)
    )
    constraint_set = PortfolioConstraintSet(constraints=(constraint,))
    zero = pd.DataFrame(
        np.zeros((3, 3)), index=case["symbols"], columns=case["symbols"]
    )
    check = evaluate_constraints(
        constraint_set,
        case["data"],
        case["weights"],
        covariance_by_model={constraint.risk_model: zero},
    )[0]
    assert check.status == "not_evaluated"
    assert check.reason_code == "RISK_UNDEFINED"
    assert check.actual is None and check.slack is None
    assert "variance" in check.message


def test_empty_set_still_validates_layer_and_stage(three_asset_constraint_case) -> None:
    empty = PortfolioConstraintSet()
    data = three_asset_constraint_case["data"]
    with pytest.raises(ValueError, match="layer"):
        evaluate_constraints(empty, data, {}, layer="user")
    with pytest.raises(ValueError, match="stage"):
        evaluate_constraints(empty, data, {}, stage="execution")


def test_evaluation_does_not_mutate_inputs(three_asset_constraint_case) -> None:
    case = three_asset_constraint_case
    weights = dict(case["weights"])
    covariance = _covariance(case)
    original_covariance = covariance.copy()
    evaluate_constraints(
        case["constraint_set"],
        case["data"],
        weights,
        covariance_by_model=_risk_models(case["constraint_set"], covariance),
    )
    assert weights == case["weights"]
    pd.testing.assert_frame_equal(covariance, original_covariance)


def test_stale_benchmark_is_rejected_before_calculation() -> None:
    as_of = dt.date(2026, 6, 30)
    data = PortfolioConstraintData(
        as_of=as_of,
        classifications={},
        benchmarks={
            "IDX": BenchmarkSnapshot(
                benchmark="IDX",
                effective_date=as_of - dt.timedelta(days=2),
                weights={"AAA": 1.0},
                source_version="v1",
            )
        },
    )
    constraint_set = PortfolioConstraintSet(
        constraints=(
            BenchmarkDeviationConstraint(
                id="fresh_only",
                benchmark="IDX",
                classification=None,
                max_abs_deviation=0.1,
                max_staleness_days=1,
            ),
        )
    )
    with pytest.raises(PortfolioConstraintError) as excinfo:
        evaluate_constraints(constraint_set, data, {"AAA": 1.0})
    assert excinfo.value.code == "CONSTRAINT_DATA_STALE"
