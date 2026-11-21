from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("member3_benchmark", ROOT / "scripts/analyze_member3_benchmark.py")
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)
EXAMPLE = ROOT / "tests/fixtures/collaboration/run-record.example.json"


def make_records(tmp_path: Path):
    base = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    records = []
    adjudications = {"schema_version": 1, "adjudications": {}}
    for repetition in (1, 2):
        for arm in module.ARMS:
            row = copy.deepcopy(base)
            row.update(record_id=f"{arm}-{repetition}", task_id="CONF-FORMULA-01",
                       paired_case_id="CONF-FORMULA-01", arm=arm, repetition=repetition,
                       mode="scripted", finished_at="2026-01-01T00:00:02Z")
            row["budget"].update(model_calls=8 if arm != "single" else 4,
                                 wall_seconds=180 if arm != "single" else 120)
            row["timings"].update(total_seconds=2.0 + repetition, request_to_terminal_seconds=1.5 + repetition)
            if arm == "two_agent":
                reviewer = copy.deepcopy(row["roles"][0])
                reviewer["role"] = "reviewer"
                row["roles"].append(reviewer)
                review_attempt = copy.deepcopy(row["attempts"][0])
                review_attempt.update(id=f"review-{repetition}", role="reviewer", round=1)
                row["attempts"].append(review_attempt)
                for field in ("model_calls", "tool_calls", "retries", "searches"):
                    row["totals"][field] = sum(role[field] for role in row["roles"])
                row["totals"]["model_calls"] = 2
            else:
                row["totals"]["model_calls"] = 1
            row["roles"][0]["model_calls"] = 1
            row["attempts"] = row["attempts"][:2] if arm == "two_agent" else row["attempts"][:1]
            row["totals"]["tool_calls"] = sum(role["tool_calls"] for role in row["roles"])
            row["totals"]["retries"] = sum(role["retries"] for role in row["roles"])
            row["totals"]["searches"] = sum(role["searches"] for role in row["roles"])
            row["timings"]["total_seconds"] += 1 if arm == "two_agent" else 0
            file = tmp_path / f"{row['record_id']}.json"
            file.write_text(json.dumps(row), encoding="utf-8")
            records.append(file)
            adjudications["adjudications"][row["record_id"]] = {
                "qualified": True,
                "quality_scores": {
                    "seeded_error_opportunities": 1,
                    "errors_detected": 1,
                    "errors_corrected": 1,
                    "false_alarms": 0,
                    "unsupported_conclusions_remaining": 0,
                },
                "basis": "Independent deterministic check of the frozen confirmation oracle and final output.",
            }
    adj_path = tmp_path / "adjudications.json"
    adj_path.write_text(json.dumps(adjudications), encoding="utf-8")
    return records, adj_path


def test_paired_analysis_includes_all_arms_and_counts_unavailable_cost_as_unknown(tmp_path):
    paths, adj_path = make_records(tmp_path)
    adjudications = module.load_adjudications(adj_path)
    records = module.load_records(paths, adjudications)
    pairs = module.validate_pairs(records)
    summary = module.analyse(records, pairs)
    assert summary["paired_trials"] == 2
    assert summary["arms"]["two_agent"]["runs"] == 2
    assert summary["arms"]["two_agent"]["qualified_tasks"] == 2
    assert summary["arms"]["two_agent"]["cost"]["status"] == "known"
    assert summary["paired_comparisons"]["two_agent_vs_single"]["qualified_completion"]["paired_observations"] == 2


def test_incomplete_arm_pair_is_rejected(tmp_path):
    paths, adj_path = make_records(tmp_path)
    adjudications = module.load_adjudications(adj_path)
    records = module.load_records(paths[:-1], {k: v for k, v in adjudications.items() if k != "two_agent-2"})
    with pytest.raises(ValueError, match="Incomplete paired task"):
        module.validate_pairs(records)


def test_budget_parity_is_enforced_between_self_check_and_two_agent(tmp_path):
    paths, adj_path = make_records(tmp_path)
    row = json.loads(paths[-1].read_text(encoding="utf-8"))
    row["budget"]["tokens"] = 999
    paths[-1].write_text(json.dumps(row), encoding="utf-8")
    adjudications = module.load_adjudications(adj_path)
    records = module.load_records(paths, adjudications)
    with pytest.raises(ValueError, match="allowances are not matched"):
        module.validate_pairs(records)


def test_unavailable_provider_cost_is_not_reported_as_zero(tmp_path):
    paths, adj_path = make_records(tmp_path)
    row = json.loads(paths[-1].read_text(encoding="utf-8"))
    unknown = {"status": "unavailable", "amount": None, "currency": None,
               "pricing_snapshot": None, "reason": "Provider did not return billing data."}
    row["roles"][1]["cost"] = unknown
    row["totals"]["cost"] = unknown
    paths[-1].write_text(json.dumps(row), encoding="utf-8")
    adjudications = module.load_adjudications(adj_path)
    records = module.load_records(paths, adjudications)
    pairs = module.validate_pairs(records)
    summary = module.analyse(records, pairs)
    assert summary["arms"]["two_agent"]["cost"]["status"] == "unavailable"
    assert summary["arms"]["two_agent"]["cost"]["total_cost"] is None
