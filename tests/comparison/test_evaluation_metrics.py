from __future__ import annotations

import json
from copy import deepcopy

import pytest

from apps.api.services.comparison.evaluation_metrics import build_report

FEATURES = {
    "financial_tools": True,
    "skills": True,
    "context_compression": True,
    "recovery": True,
    "multi_agent": False,
}


def _record(
    task="risk-1",
    config="B",
    rep=0,
    *,
    status="completed",
    elapsed=10.0,
    source="double",
    cost=0.0,
    safety=None,
):
    trial_id = f"{task}-{config}-{rep}"
    base_config = config.split("-", 1)[0]
    record = {
        "trial_id": trial_id,
        "config": base_config,
        "task_id": task,
        "task_category": "risk_execution",
        "timestamps": {"duration_s": elapsed},
        "reproducibility": {
            "repetition_index": rep,
            "interleave_seed": 42,
            "task_version": "task-v1",
            "framework_version": f"{base_config}-v1",
        },
        "initial_state": {"initial_data_hash": "b" * 64},
        "conditions": {
            "variant": config,
            "cache": "cold",
            "mode": "automated",
            "source": source,
            "stage": "development",
            "limits": {"time_s": 30},
            "model_id": "candidate-model",
            "provider": "same-provider",
            "reasoning": "default",
            "hardware": "same-machine",
            "permissions_sha256": "f" * 64,
            "assets_sha256": "e" * 64,
            "search_policy": "disabled",
            "features": deepcopy(FEATURES),
        },
        "resources": {"cost_usd": cost, "human_intervention_count": 0, "human_active_minutes": 0},
        "execution": {"status": status, "events": []},
        "results": {"retries": 0, "errors": []},
        "budget": {"enabled": source == "live", "spent_usd": 0.0},
        "is_complete": True,
    }
    grade = {"outcome": status, "qualified": status == "completed", "safety": safety or {}}
    return record, grade


def _report(pairs, *, plan=None):
    return build_report(
        [record for record, _ in pairs],
        {record["trial_id"]: grade for record, grade in pairs},
        plan=plan,
    )


def _plan(tasks=None, *, reps=1, kind="same_model", right="C", feature=None):
    comparison = {"id": "test-comparison", "left": "B", "right": right, "kind": kind}
    if feature is not None:
        comparison["feature"] = feature
    return {
        "comparisons": [comparison],
        "task_ids": tasks or ["risk-1"],
        "repetitions": reps,
        "stage": "development",
        "cache": "cold",
        "source": "double",
        "mode": "automated",
        "thresholds": {"bootstrap_samples": 100},
    }


def test_all_attempt_cost_includes_failures_search_and_roles_and_deduplicates_charge_ids():
    completed = _record(source="live", cost=0.03)
    completed[0]["results"]["retries"] = 1
    charges = [
        {
            "charge_id": "a",
            "kind": "model",
            "role": "planner",
            "attempt_id": "id1",
            "status": "failed",
            "cost_usd": 0.01,
        },
        {
            "charge_id": "b",
            "kind": "search",
            "role": "researcher",
            "attempt_id": "id2",
            "status": "completed",
            "cost_usd": 0.03,
        },
        {
            "charge_id": "c",
            "kind": "model",
            "role": "researcher",
            "attempt_id": "id2",
            "status": "completed",
            "cost_usd": 0.04,
        },
    ]
    completed[0]["resources"].update(cost_ledger_complete=True, cost_ledger=charges + [charges[0]])
    timeout = _record(rep=1, status="timeout", source="live", elapsed=30)
    timeout[0]["resources"].update(
        cost_ledger_complete=True,
        cost_ledger=[
            {
                "charge_id": "failed-call",
                "kind": "model",
                "role": "planner",
                "attempt_id": "id1",
                "status": "timeout",
                "cost_usd": 0.05,
            }
        ],
    )
    timeout[1]["infrastructure_failure"] = True
    result = _report([completed, timeout])
    first = result["trial_outcomes"][0]
    assert first["cost_usd"] == pytest.approx(0.08)
    assert first["recorded_role_count"] == 2
    assert first["cost_by_kind_usd"] == {"model": 0.05, "search": 0.03}
    assert first["attempts_with_recorded_charges"][0]["recorded_charge_statuses"] == ["failed"]
    summary = result["summaries"][0]
    assert summary["attempt_count"] == 3
    assert summary["total_cost_usd"] == pytest.approx(0.13)
    assert summary["cost_per_qualified_task_usd"] == pytest.approx(0.13)
    assert summary["infrastructure_failure_count"] == 1
    assert summary["elapsed_seconds"] == {"observations": 2, "median": 20, "p90": 28}
    assert summary["outcome_counts"] == {"completed": 1, "timeout": 1}


def test_no_qualification_is_undefined_and_unattested_bill_is_never_zero():
    timeout = _record(status="timeout", source="live", cost=1.25)
    timeout[1].update(outcome="completed", qualified=True)
    result = _report([timeout])
    trial = result["trial_outcomes"][0]
    assert not trial["qualified"] and trial["outcome"] == "timeout"
    summary = result["summaries"][0]
    assert summary["recorded_cost_usd"] == 1.25
    assert summary["total_cost_usd"] is None
    assert summary["cost_per_qualified_task_usd"] is None
    assert summary["cost_per_qualified_task_status"] == "no_qualified_tasks"


def test_conflicting_duplicate_charge_keeps_cost_unknown():
    case = _record(source="live", cost=0.02)
    case[0]["resources"].update(
        cost_ledger_complete=True,
        cost_ledger=[
            {"charge_id": "reused", "attempt_id": "first", "cost_usd": 0.01},
            {"charge_id": "reused", "attempt_id": "first", "cost_usd": 0.02},
        ],
    )
    trial = _report([case])["trial_outcomes"][0]
    assert trial["cost_status"] == "partial"
    assert trial["cost_usd"] is None
    assert trial["recorded_cost_usd"] == 0.02
    assert trial["attempts_with_recorded_charges"][0]["recorded_cost_usd"] is None


def test_runtime_blocked_can_have_a_graded_semantic_completion_and_terminal_failures_keep_cost():
    blocked = _record(status="blocked")
    blocked[1].update(qualified=True, outcome="completed", semantic_outcome="completed")
    capped = _record(rep=1, status="failed", cost=0.2)
    capped[0]["execution"]["events"] = [{"cost_usd": 0.2}, {"cost_usd": 0.3}]
    result = _report([blocked, capped])
    first, second = result["trial_outcomes"]
    assert first["qualified"] and first["runtime_status"] == "blocked"
    assert first["semantic_outcome"] == "completed"
    assert not second["qualified"] and second["cost_usd"] == 0.5
    assert result["summaries"][0]["cost_per_qualified_task_usd"] == 0.5


def test_recorded_plan_lists_missing_duplicate_and_unplanned_cells_and_does_not_pool_strata():
    timeout = _record(config="C", status="timeout", elapsed=45, cost=0.5)
    warm = _record(rep=1)
    warm[0]["conditions"]["cache"] = "warm"
    human = _record(rep=1)
    human[0]["trial_id"] += "-human"
    human[0]["conditions"]["mode"] = "human"
    duplicate = _record(rep=2)
    duplicate[0]["trial_id"] += "-duplicate"
    outside = _record(task="new-unplanned-risk")
    result = _report(
        [
            _record(),
            timeout,
            warm,
            human,
            _record(rep=2),
            duplicate,
            _record(config="C", rep=3),
            outside,
        ],
        plan=_plan(reps=4),
    )
    assert len(result["trial_outcomes"]) == 8
    assert {(x["cache"], x["mode"]) for x in result["summaries"]} == {
        ("cold", "automated"),
        ("warm", "automated"),
        ("cold", "human"),
    }
    comparison = result["comparisons"][0]
    assert comparison["pair_status_counts"] == {
        "duplicate_cell": 1,
        "missing_both": 1,
        "missing_left": 1,
        "paired": 1,
        "unplanned_cell": 1,
    }
    assert comparison["right"]["total_cost_usd"] == 0.5
    assert comparison["paired_qualified_rate_difference"]["right_minus_left"] == -1.0
    assert comparison["quality_at_same_budget"]["status"] == "not_established"
    assert comparison["cost_time_at_same_quality"]["status"] == "not_established"


def test_same_budget_quality_diff_and_same_quality_cost_use_failed_pair_members():
    cases = []
    task_ids = [f"risk-{i}" for i in range(14)]
    for task_id in task_ids:
        for config in ("B", "C"):
            cases.append(_record(task_id, config, 0, elapsed=20, cost=1))
            cases.append(
                _record(
                    task_id, config, 1, status="timeout", elapsed=40, cost=1 if config == "B" else 2
                )
            )
    plan = _plan(task_ids, reps=2)
    plan["thresholds"].update(quality_floor=0.5, equivalence_margin=0.25)
    comparison = _report(cases, plan=plan)["comparisons"][0]
    qual = comparison["quality_at_same_budget"]
    assert qual["status"] == "descriptive"
    interval = qual["paired_qualified_rate_difference"]
    assert interval["observed_pairs"] == 28
    assert interval["confidence_interval"][0] < 0 < interval["confidence_interval"][1]
    efficiency = comparison["cost_time_at_same_quality"]
    assert efficiency["status"] == "descriptive"
    assert efficiency["right_minus_left_cost_per_qualified_task_usd"] == 1
    assert efficiency["all_pair_cost_usd"]["right_minus_left"] == 0.5
    assert efficiency["all_pair_elapsed_seconds"]["observed_pairs"] == 28


def test_formal_latency_uses_observer_end_to_end_and_does_not_block_complete_cost():
    task_ids = [f"risk-{i}" for i in range(4)]
    pairs = []
    for task_id in task_ids:
        for config in ("B", "C"):
            for repetition in range(2):
                case = _record(task_id, config, repetition, source="live")
                case[0]["conditions"]["stage"] = "confirmation"
                case[0]["resources"].update(cost_ledger_complete=True, cost_ledger=[])
                pairs.append(case)
    plan = _plan(task_ids, reps=2)
    plan.update(stage="confirmation", source="live", protocol_verified=True)
    plan["thresholds"]["equivalence_margin"] = 0.6
    summary = _report(pairs, plan=plan)["comparisons"][0]["cost_time_at_same_quality"]
    assert summary["status"] == "partial"
    assert summary["cost_status"] == "confirmation"
    assert summary["elapsed_status"] == "not_established"
    assert summary["all_pair_elapsed_seconds"] is None
    assert "end_to_end_latency_unverified" in summary["reasons"]
    for record, _ in pairs:
        record["resources"]["end_to_end_seconds"] = 16 if record["config"] == "B" else 12
    report = _report(pairs, plan=plan)
    summary = report["comparisons"][0]["cost_time_at_same_quality"]
    assert summary["status"] == "confirmation"
    assert summary["elapsed_source"] == "observer_end_to_end"
    assert summary["all_pair_elapsed_seconds"]["right_minus_left"] == -4
    assert report["summaries"][0]["end_to_end_seconds"] == {
        "observations": 8,
        "median": 16,
        "p90": 16,
    }


def test_ablation_rejects_multiple_feature_changes_and_distinguishes_product():
    control = _record(config="B-no-skills")
    control[0]["conditions"]["features"]["skills"] = False
    plan = _plan(kind="ablation", right="B-no-skills", feature="skills")
    isolated = _report([_record(), control], plan=plan)["comparisons"][0]
    assert isolated["quality_at_same_budget"]["status"] == "descriptive"
    control[0]["conditions"]["features"]["financial_tools"] = False
    confounded = _report([_record(), control], plan=plan)["comparisons"][0]
    assert "ablation_changes_unisolated" in confounded["quality_at_same_budget"]["reasons"]
    other_model = _record(config="C")
    other_model[0]["conditions"]["model_id"] = "some-other-model"
    product = _report([_record(), other_model], plan=_plan(kind="product"))["comparisons"][0]
    assert product["quality_at_same_budget"]["status"] == "descriptive"
    same_model = _report([_record(), other_model], plan=_plan())["comparisons"][0]
    assert "unmatched_model_conditions" in same_model["quality_at_same_budget"]["reasons"]


def test_reproducibility_safety_and_report_only_use_allowlisted_fields():
    successful = _record()
    successful[0]["results"]["errors"] = [{"content": "never-export-raw-portfolio-or-auth"}]
    successful[1]["evidence_sha256"] = "a" * 64
    successful[0]["resources"].update(human_intervention_count=1, human_active_minutes=4)
    again = _record(rep=1)
    again[1]["evidence_sha256"] = "a" * 64
    unsafe = _record(
        task="unsafe-risk",
        safety={"unauthorized_actions": 1, "fabrication_events": 2, "duplicate_executions": 1},
    )
    result = _report([again, unsafe, successful])
    summary = result["summaries"][0]
    assert summary["qualified_count"] == 2
    assert summary["reproducibility"]["evidence_reproducibility_rate"] == 1.0
    assert summary["safety"]["fabrication_events"] == {"count": 2, "observations": 1}
    assert summary["human_active_minutes"]["observations"] == 3
    assert "never-export-raw-portfolio-or-auth" not in json.dumps(result)


def test_empty_and_duplicate_input():
    plan = _plan(reps=2)
    result = build_report([], {}, plan=plan)
    assert result["comparisons"][0]["pair_status_counts"] == {"missing_both": 2}
    case = _record()
    with pytest.raises(ValueError, match="Duplicate trial ID"):
        _report([case, case])
