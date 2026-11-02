from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest

from alphalab.sdk.v1 import (
    BenchmarkDeviationConstraint,
    ConstraintCheck,
    GroupWeightConstraint,
    PortfolioConstraintData,
    PortfolioConstraintError,
    PortfolioConstraintSet,
    RiskBudgetConstraint,
    RiskModelSpec,
)
from alphalab.sdk.v1.constraints import (
    UNCLASSIFIED_GROUP,
    BenchmarkSnapshot,
    ClassificationSnapshot,
    validate_constraint_data,
)

AS_OF = dt.date(2026, 6, 30)


def _classification(groups=None, **overrides) -> ClassificationSnapshot:
    values = {
        "classification": "test_scheme:1",
        "effective_date": AS_OF,
        "groups": groups if groups is not None else {"AAA": "TECH", "BBB": "TECH", "CCC": "FIN"},
        "source_version": "v1",
    }
    return ClassificationSnapshot(**{**values, **overrides})


def _benchmark(weights=None, **overrides) -> BenchmarkSnapshot:
    values = {
        "benchmark": "IDX",
        "effective_date": AS_OF,
        "weights": weights if weights is not None else {"AAA": 0.2, "BBB": 0.3, "CCC": 0.5},
        "source_version": "v1",
    }
    return BenchmarkSnapshot(**{**values, **overrides})


def _data(classification=None, benchmark=None, **overrides) -> PortfolioConstraintData:
    classification = classification or _classification()
    benchmark = benchmark or _benchmark()
    values = {
        "as_of": AS_OF,
        "classifications": {classification.classification: classification},
        "benchmarks": {benchmark.benchmark: benchmark},
    }
    return PortfolioConstraintData(**{**values, **overrides})


def _code(excinfo: pytest.ExceptionInfo) -> str:
    return excinfo.value.code


def test_shared_fixture_matches_contract_worked_example(three_asset_constraint_case) -> None:
    case = three_asset_constraint_case
    weights = np.array([case["weights"][symbol] for symbol in case["symbols"]])
    benchmark = case["data"].benchmarks["IDX"].weights
    groups = case["data"].group_map("test_scheme:1", case["symbols"])
    covariance = case["annual_covariance"]
    variance = weights @ covariance @ weights
    contributions = weights * (covariance @ weights) / variance
    expected = case["expected"]

    tech = [index for index, symbol in enumerate(case["symbols"]) if groups[symbol] == "TECH"]
    assert weights[tech].sum() == pytest.approx(expected["group_weight"]["TECH"])
    assert weights[tech].sum() - sum(benchmark[case["symbols"][i]] for i in tech) == pytest.approx(
        expected["benchmark_deviation"]["TECH"]
    )
    assert contributions == pytest.approx(
        [expected["risk_contribution"][symbol] for symbol in case["symbols"]], abs=5e-5
    )
    assert contributions[tech].sum() == pytest.approx(expected["tech_risk_contribution"], abs=5e-5)
    assert np.sqrt(variance) == pytest.approx(expected["portfolio_volatility"], abs=5e-5)
    validate_constraint_data(case["constraint_set"], case["data"], case["weights"])


def test_constraint_set_round_trips_through_canonical_json(three_asset_constraint_case) -> None:
    original = three_asset_constraint_case["constraint_set"]
    payload = json.loads(json.dumps(original.to_dict()))
    restored = PortfolioConstraintSet.from_dict(payload)

    assert restored == original
    assert restored.constraint_set_sha256 == original.constraint_set_sha256
    assert original.classifications == ("test_scheme:1",)
    assert original.benchmarks == ("IDX",)


def test_constraint_set_hash_ignores_numeric_spelling_but_not_values() -> None:
    def build(upper):
        return PortfolioConstraintSet(
            constraints=(GroupWeightConstraint(id="cap", classification="sw:1", upper=upper),)
        )

    assert build(1).constraint_set_sha256 == build(1.0).constraint_set_sha256
    assert build(0.3).constraint_set_sha256 != build(0.31).constraint_set_sha256


def test_security_level_subjects_use_symbol_casing() -> None:
    constraint = BenchmarkDeviationConstraint(
        id="active",
        benchmark=" idx ",
        classification=None,
        groups=("aaa",),
        max_abs_deviation=0.02,
        max_staleness_days=0,
    )
    assert constraint.benchmark == "IDX"
    assert constraint.groups == ("AAA",)


@pytest.mark.parametrize(
    "build",
    [
        lambda: GroupWeightConstraint(id="_private", classification="sw:1", upper=0.3),
        lambda: GroupWeightConstraint(id="cap", classification="sw", upper=0.3),
        lambda: GroupWeightConstraint(id="cap", classification="sw:1"),
        lambda: GroupWeightConstraint(id="cap", classification="sw:1", lower=0.4, upper=0.3),
        lambda: GroupWeightConstraint(id="cap", classification="sw:1", upper=1.2),
        lambda: GroupWeightConstraint(id="cap", classification="sw:1", upper=True),
        lambda: GroupWeightConstraint(id="cap", classification="sw:1", upper=float("nan")),
        lambda: GroupWeightConstraint(id="cap", classification="sw:1", groups=(), upper=0.3),
        lambda: GroupWeightConstraint(
            id="cap", classification="sw:1", groups=("A", "A"), upper=0.3
        ),
        lambda: GroupWeightConstraint(
            id="cap", classification="sw:1", upper=0.3, missing_policy="ignore"
        ),
        lambda: BenchmarkDeviationConstraint(
            id="dev",
            benchmark="IDX",
            classification=None,
            max_abs_deviation=0.0,
            max_staleness_days=5,
        ),
        lambda: BenchmarkDeviationConstraint(
            id="dev",
            benchmark="IDX",
            classification=None,
            max_abs_deviation=0.05,
            max_staleness_days=-1,
        ),
        lambda: RiskModelSpec(lookback=20, minimum_observations=30),
        lambda: RiskModelSpec(lookback=60, minimum_observations=40, estimator="shrinkage"),
        lambda: RiskBudgetConstraint(
            id="risk", classification=None, upper=0.4, risk_model={"lookback": 60}
        ),
        lambda: PortfolioConstraintSet(
            constraints=(
                GroupWeightConstraint(id="cap", classification="sw:1", upper=0.3),
                GroupWeightConstraint(id="cap", classification="sw:1", upper=0.4),
            )
        ),
        lambda: PortfolioConstraintSet(schema_version=2),
        lambda: PortfolioConstraintSet(tolerance=1e-3),
        lambda: PortfolioConstraintSet(tolerance=0.0),
    ],
)
def test_invalid_constraint_specs_fail_with_stable_code(build) -> None:
    with pytest.raises(PortfolioConstraintError) as excinfo:
        build()
    assert _code(excinfo) == "CONSTRAINT_SPEC_INVALID"


@pytest.mark.parametrize(
    "payload",
    [
        {"constraints": [{"kind": "turnover", "id": "x"}]},
        {
            "constraints": [
                {
                    "kind": "group_weight",
                    "id": "x",
                    "classification": "sw:1",
                    "upper": 0.3,
                    "priority": 1,
                }
            ]
        },
        {"constraints": [{"kind": "group_weight", "id": "x"}]},
        {"constraints": "group_weight"},
        {"constraints": [], "owner": "someone"},
    ],
)
def test_constraint_set_from_dict_rejects_unknown_or_incomplete_input(payload) -> None:
    with pytest.raises(PortfolioConstraintError) as excinfo:
        PortfolioConstraintSet.from_dict(payload)
    assert _code(excinfo) == "CONSTRAINT_SPEC_INVALID"


def test_constraint_data_versions_contents_and_round_trips() -> None:
    data = _data()
    restored = PortfolioConstraintData.from_dict(json.loads(json.dumps(data.to_dict())))

    assert restored.data_version == data.data_version
    assert len(data.data_version) == 64
    assert _data(benchmark=_benchmark(source_version="v2")).data_version != data.data_version


def test_constraint_data_rejects_a_mismatched_version() -> None:
    payload = _data().to_dict()
    payload["benchmarks"]["IDX"]["weights"]["AAA"] = 0.21
    payload["benchmarks"]["IDX"]["weights"]["CCC"] = 0.49
    with pytest.raises(PortfolioConstraintError) as excinfo:
        PortfolioConstraintData.from_dict(payload)
    assert _code(excinfo) == "CONSTRAINT_DATA_VERSION_INVALID"


@pytest.mark.parametrize(
    ("build", "code"),
    [
        (
            lambda: _data(
                classification=_classification(effective_date=AS_OF + dt.timedelta(days=1))
            ),
            "CONSTRAINT_DATA_FUTURE",
        ),
        (
            lambda: _data(benchmark=_benchmark(effective_date="2026-07-01")),
            "CONSTRAINT_DATA_FUTURE",
        ),
        (
            lambda: _classification(groups=[("AAA", "TECH"), ("aaa", "FIN")]),
            "CONSTRAINT_DATA_DUPLICATE",
        ),
        (lambda: _benchmark(weights=[("AAA", 0.5), ("AAA", 0.5)]), "CONSTRAINT_DATA_DUPLICATE"),
        (lambda: _classification(groups={"AAA": UNCLASSIFIED_GROUP}), "CONSTRAINT_DATA_INVALID"),
        (lambda: _classification(groups={"AAA": " "}), "CONSTRAINT_DATA_INVALID"),
        (lambda: _classification(source_version=""), "CONSTRAINT_DATA_INVALID"),
        (
            lambda: _benchmark(weights={"AAA": 20.0, "BBB": 30.0, "CCC": 50.0}),
            "CONSTRAINT_DATA_INVALID",
        ),
        (
            lambda: _benchmark(weights={"AAA": 0.6, "BBB": 0.6, "CCC": -0.2}),
            "CONSTRAINT_DATA_INVALID",
        ),
        (lambda: _benchmark(weights={"AAA": float("nan"), "BBB": 1.0}), "CONSTRAINT_DATA_INVALID"),
        (lambda: _benchmark(effective_date="30/06/2026"), "CONSTRAINT_DATA_INVALID"),
        (
            lambda: PortfolioConstraintData(
                as_of=AS_OF, classifications={"other:1": _classification()}, benchmarks={}
            ),
            "CONSTRAINT_DATA_INVALID",
        ),
    ],
)
def test_invalid_constraint_data_fails_with_stable_code(build, code) -> None:
    with pytest.raises(PortfolioConstraintError) as excinfo:
        build()
    assert _code(excinfo) == code


def test_benchmark_sum_band_accepts_vendor_rounding() -> None:
    snapshot = _benchmark(weights={"AAA": 0.2004, "BBB": 0.3, "CCC": 0.5})
    assert sum(snapshot.weights.values()) == pytest.approx(1.0004)


def _set(*constraints) -> PortfolioConstraintSet:
    return PortfolioConstraintSet(constraints=constraints)


def test_validation_reports_missing_classification_and_benchmark() -> None:
    data = PortfolioConstraintData(as_of=AS_OF, classifications={}, benchmarks={})
    with pytest.raises(PortfolioConstraintError) as excinfo:
        validate_constraint_data(
            _set(GroupWeightConstraint(id="cap", classification="test_scheme:1", upper=0.3)),
            data,
            ["AAA"],
        )
    assert (_code(excinfo), excinfo.value.constraint_id) == ("CONSTRAINT_DATA_MISSING", "cap")

    with pytest.raises(PortfolioConstraintError) as excinfo:
        validate_constraint_data(
            _set(
                BenchmarkDeviationConstraint(
                    id="dev",
                    benchmark="IDX",
                    classification=None,
                    max_abs_deviation=0.05,
                    max_staleness_days=31,
                )
            ),
            data,
            ["AAA"],
        )
    assert _code(excinfo) == "CONSTRAINT_DATA_MISSING"


def test_validation_rejects_stale_benchmark_weights() -> None:
    data = _data(benchmark=_benchmark(effective_date=AS_OF - dt.timedelta(days=32)))
    constraint = BenchmarkDeviationConstraint(
        id="dev",
        benchmark="IDX",
        classification=None,
        max_abs_deviation=0.05,
        max_staleness_days=31,
    )
    with pytest.raises(PortfolioConstraintError) as excinfo:
        validate_constraint_data(_set(constraint), data, ["AAA"])
    assert _code(excinfo) == "CONSTRAINT_DATA_STALE"

    fresh = _data(benchmark=_benchmark(effective_date=AS_OF - dt.timedelta(days=31)))
    validate_constraint_data(_set(constraint), fresh, ["AAA"])


def test_unclassified_holdings_follow_the_missing_policy() -> None:
    data = _data(classification=_classification(groups={"AAA": "TECH", "BBB": "TECH"}))
    rejecting = GroupWeightConstraint(id="cap", classification="test_scheme:1", upper=0.6)
    with pytest.raises(PortfolioConstraintError) as excinfo:
        validate_constraint_data(_set(rejecting), data, ["AAA", "DDD"])
    assert (_code(excinfo), excinfo.value.subject) == ("UNCLASSIFIED_SECURITY", "DDD")

    bucketed = GroupWeightConstraint(
        id="cap", classification="test_scheme:1", upper=0.6, missing_policy="unclassified_group"
    )
    validate_constraint_data(_set(bucketed), data, ["AAA", "DDD"])
    assert data.group_map("test_scheme:1", ["aaa", "DDD"], missing_policy="unclassified_group") == {
        "AAA": "TECH",
        "DDD": UNCLASSIFIED_GROUP,
    }


def test_grouped_deviation_also_requires_groups_for_benchmark_members() -> None:
    # CCC is not held but carries benchmark weight, so its group is still required.
    data = _data(classification=_classification(groups={"AAA": "TECH", "BBB": "TECH"}))
    constraint = BenchmarkDeviationConstraint(
        id="dev",
        benchmark="IDX",
        classification="test_scheme:1",
        max_abs_deviation=0.05,
        max_staleness_days=31,
    )
    with pytest.raises(PortfolioConstraintError) as excinfo:
        validate_constraint_data(_set(constraint), data, ["AAA"])
    assert (_code(excinfo), excinfo.value.subject) == ("UNCLASSIFIED_SECURITY", "CCC")


def _check(**overrides) -> dict:
    values = {
        "layer": "project",
        "constraint_id": "tech_cap",
        "kind": "group_weight",
        "stage": "target",
        "subject": "TECH",
        "lower": None,
        "upper": 0.6,
        "tolerance": 1e-6,
        "constraint_set_sha256": "0" * 64,
    }
    return {**values, **overrides}


def test_constraint_check_from_bounds_follows_contract_slack() -> None:
    failed = ConstraintCheck.from_bounds(actual=0.70, **_check())
    assert failed.status == "fail"
    assert failed.slack == pytest.approx(-0.10)

    within_tolerance = ConstraintCheck.from_bounds(actual=0.6000005, **_check())
    assert within_tolerance.status == "pass"
    assert json.loads(json.dumps(within_tolerance.to_dict()))["status"] == "pass"


@pytest.mark.parametrize(
    "fields",
    [
        _check(actual=0.7, slack=-0.1, status="pass"),
        _check(actual=0.5, slack=0.1, status="fail"),
        _check(actual=0.7, slack=0.0, status="fail"),
        _check(actual=0.36, slack=-0.24, status="improved"),
        _check(actual=None, slack=None, status="not_evaluated"),
        _check(actual=0.5, slack=0.1, status="pass", stage="execution"),
        _check(actual=0.5, slack=0.1, status="pass", layer="user"),
        _check(actual=0.5, slack=0.1, status="pass", kind="sector"),
        _check(actual=float("inf"), slack=0.1, status="pass"),
    ],
)
def test_constraint_check_rejects_inconsistent_records(fields) -> None:
    with pytest.raises(ValueError):
        ConstraintCheck(**fields)


def test_constraint_check_improved_and_not_evaluated_records() -> None:
    improved = ConstraintCheck(
        **_check(
            stage="planned", upper=0.30, actual=0.36, slack=-0.06, reference=0.40, status="improved"
        )
    )
    assert improved.reference == pytest.approx(0.40)

    skipped = ConstraintCheck(
        **_check(
            kind="risk_budget",
            actual=None,
            slack=None,
            status="not_evaluated",
            reason_code="RISK_UNDEFINED",
        )
    )
    assert skipped.reason_code == "RISK_UNDEFINED"
