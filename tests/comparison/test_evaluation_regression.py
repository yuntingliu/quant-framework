"""An offline regression set for version attribution, scope, and paid-run gating."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.api.services.comparison.grading import GRADER_VERSION, grade_trial
from apps.api.services.comparison.regression import (
    check_paid_extension,
    compare_regression_snapshots,
    make_regression_snapshot,
)

_VERSIONS = {"public-audit": "task-v1", "public-refusal": "task-v1"}
_DEMO_RECORDS = Path(__file__).resolve().parents[2] / "benchmark" / "evaluation" / "demo_records.json"


def _evaluated_case(
    task_id="public-audit",
    *,
    repetition=0,
    config="A",
    passed=True,
    **conditions,
):
    trial = {
        "trial_id": f"trial-{task_id}-{config}-{repetition}",
        "task_id": task_id,
        "config": config,
        "reproducibility": {"repetition_index": repetition},
        "conditions": {"cache": "cold", "source": "double", "stage": "dev", **conditions},
        "execution": {"events": [{"body": "PRIVATE_PROVIDER_PAYLOAD"}]},
        "results": {"artifacts": [{"path": "/private/workspace/report.md"}]},
    }
    grade = {
        "grader_version": "grader-v1",
        "trial_id": trial["trial_id"],
        "task_id": task_id,
        "qualified": passed,
        "outcome": "qualified" if passed else "unqualified",
        "runtime_status": "completed",
        "infrastructure_failure": False,
        "checks": [
            {
                "id": "annualisation",
                "blocking": True,
                "status": "passed" if passed else "failed",
                "actual": "PRIVATE_FINDING",
            }
        ],
        "safety": {"unauthorized_actions": 0, "fabrication_events": 0, "duplicate_executions": 0},
    }
    return trial, grade


def _snapshot(*rows, version="system-v1", tasks=None):
    return make_regression_snapshot(
        rows,
        suite_version="suite-v1",
        evaluator_version="grader-v1",
        system_version=version,
        public_task_versions=_VERSIONS if tasks is None else tasks,
    )


def test_regression_identifies_failed_check_and_exact_version_pair():
    baseline = _snapshot(_evaluated_case(), _evaluated_case("public-refusal"))
    candidate = _snapshot(
        _evaluated_case(passed=False),
        _evaluated_case("public-refusal"),
        version="system-v2",
    )

    diff = compare_regression_snapshots(baseline, candidate)

    assert diff["has_regression"] is True
    assert diff["requires_rebaseline"] is False
    assert diff["versions"]["baseline"]["system_version"] == "system-v1"
    assert diff["versions"]["candidate"]["system_version"] == "system-v2"
    assert diff["unchanged_cases"] == 1
    (regressed,) = diff["changed_cases"]
    assert regressed["case"]["task_id"] == "public-audit"
    assert regressed["state"] == "regressed"
    assert regressed["reasons"] == ["qualification_lost", "blocking_check_lost:annualisation"]
    assert regressed["baseline"]["task_version"] == "task-v1"
    assert regressed["candidate"]["framework_version"] == "system-v2"


def test_snapshot_separates_repetitions_and_conditions_and_flags_missing_cases():
    cold = _evaluated_case()
    warm = _evaluated_case(cache="warm")
    human = _evaluated_case(mode="human")
    repeated = _evaluated_case(repetition=1)
    baseline = _snapshot(cold, warm, human)
    candidate = _snapshot(cold, human, repeated)

    diff = compare_regression_snapshots(baseline, candidate)

    assert diff["has_regression"] is True
    assert diff["unchanged_cases"] == 2
    dropped, added = diff["changed_cases"]
    assert dropped["case"]["cache"] == "warm"
    assert dropped["state"] == "missing"
    assert dropped["candidate"] is None
    assert added["state"] == "added"
    assert added["case"]["repetition_index"] == 1


def test_changed_task_contract_is_incomparable_and_requires_rebaseline():
    baseline = _snapshot(_evaluated_case())
    candidate = _snapshot(_evaluated_case(passed=False), tasks={"public-audit": "task-v2"})

    diff = compare_regression_snapshots(baseline, candidate)

    assert diff["has_regression"] is False
    assert diff["requires_rebaseline"] is True
    assert diff["changed_cases"][0]["state"] == "contract_changed"
    assert diff["changed_cases"][0]["baseline"]["task_version"] == "task-v1"
    assert diff["changed_cases"][0]["candidate"]["task_version"] == "task-v2"


def test_safety_deterioration_fails_even_when_both_cases_are_unqualified():
    baseline_case = _evaluated_case(passed=False)
    after = _evaluated_case(passed=False)
    after[1]["safety"]["duplicate_executions"] = 1

    diff = compare_regression_snapshots(_snapshot(baseline_case), _snapshot(after))

    assert diff["has_regression"] is True
    assert diff["changed_cases"][0]["reasons"] == ["safety_count_increased:duplicate_executions"]


def test_snapshot_and_diff_exclude_private_tasks_and_raw_payloads():
    public = _evaluated_case()
    private = _evaluated_case("private-unpublished-sample")
    baseline = _snapshot(public, private)
    candidate = _snapshot(_evaluated_case(passed=False))
    candidate["cases"][0]["extra_field"] = "PRIVATE_METADATA"

    serialized = json.dumps((baseline, compare_regression_snapshots(baseline, candidate)))

    assert len(baseline["cases"]) == 1
    for secret in ("private-unpublished-sample", "PRIVATE_", "/private/", "trial-public-audit"):
        assert secret not in serialized


def test_snapshot_rejects_ambiguous_cases_and_mismatched_grades():
    case = _evaluated_case()
    with pytest.raises(ValueError, match="Duplicate task"):
        _snapshot(case, case)
    with pytest.raises(ValueError, match="does not belong"):
        _snapshot((case[0], _evaluated_case(config="B")[1]))
    with pytest.raises(ValueError, match="no trials"):
        _snapshot(_evaluated_case("private-unpublished-sample"))


def test_neutral_record_without_semantic_evidence_is_unqualified_in_regression():
    trial = json.loads(_DEMO_RECORDS.read_text(encoding="utf-8"))[0]
    assert trial["resources"]["cost_usd"] == 0
    assert trial["budget"]["enabled"] is False
    task = {
        "task_id": trial["task_id"],
        "expected_outcome": "completed",
        "allowed_operations": {},
        "budget": {},
        "target_artifacts": [],
        "pass_conditions": [],
    }
    grade = grade_trial(task, trial)
    snapshot = make_regression_snapshot(
        [(trial, grade)],
        suite_version="demo-v1",
        evaluator_version=GRADER_VERSION,
        system_version="system-v1",
        public_task_versions={trial["task_id"]: "task-v1"},
    )
    assert snapshot["cases"][0]["source"] == "double"
    assert snapshot["cases"][0]["blocking_checks"]["runtime:terminal"] == "passed"
    assert snapshot["cases"][0]["blocking_checks"]["task:semantic-outcome"] == "failed"
    assert compare_regression_snapshots(snapshot, snapshot)["unchanged_cases"] == 1


def test_paid_extension_does_not_even_consume_a_plan_by_default():
    def plan():
        pytest.fail("A default regression must never inspect or dispatch a paid plan")
        yield 1.0

    assert check_paid_extension(trial_cost_caps_usd=plan()) is False


@pytest.mark.parametrize("invalid", [None, True, 0, -1, float("inf"), float("nan")])
def test_paid_extension_needs_a_finite_positive_total_budget(invalid):
    with pytest.raises(ValueError, match="finite positive"):
        check_paid_extension(enabled=True, total_budget_usd=invalid, trial_cost_caps_usd=[0.1])


def test_paid_extension_checks_entire_plan_and_blocks_stub_adapters():
    with pytest.raises(ValueError, match="cost cap for each"):
        check_paid_extension(enabled=True, total_budget_usd=0.3)
    with pytest.raises(ValueError, match="exceeds"):
        check_paid_extension(enabled=True, total_budget_usd=0.2, trial_cost_caps_usd=[0.1, 0.2])
    with pytest.raises(RuntimeError, match="verified real adapters"):
        check_paid_extension(enabled=True, total_budget_usd=0.3, trial_cost_caps_usd=[0.1, 0.2])
    assert (
        check_paid_extension(
            enabled=True,
            total_budget_usd=0.3,
            trial_cost_caps_usd=[0.1, 0.2],
            runner_supports_paid=True,
        )
        is True
    )
