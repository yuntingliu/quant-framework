"""Protect independent arithmetic, input/oracle separation and record accounting."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import ValidationError

from alphalab.collaboration import (
    check_development_oracle,
    fixture_facts,
    load_agent_input,
    validate_run_record,
    verify_development_corpus,
)

CORPUS = Path(__file__).resolve().parents[1] / "fixtures/collaboration/dev"
EXAMPLE = CORPUS.parent / "run-record.example.json"


def test_corpus_hashes_oracle_and_citations_are_consistent():
    result = check_development_oracle(CORPUS)
    assert result["case_count"] == 8
    assert result["oracle_checks"] == "passed"
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    assert all(case["coverage"] for case in manifest["cases"])
    assert "Member 3" in manifest["holdout"]


def test_exact_agent_payload_contains_neither_oracle_nor_manifest_labels():
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    for case in manifest["cases"]:
        payload = load_agent_input(CORPUS, case["fixture_id"])
        assert set(payload) == {"fixture_id", "question", "candidate", "evidence", "required_evidence_ids"}
        encoded = json.dumps(payload)
        for forbidden in ("expected_status", "expected_categories", "rationale", "oracle", "owner_phase"):
            assert forbidden not in encoded
        assert set(payload["evidence"]) == set(case["allowed_evidence_ids"])


@pytest.mark.parametrize("relative", ["inputs/DEV-FORMULA-01.json", "oracle.json", "protocol-scenarios.json"])
def test_mutating_any_frozen_input_or_oracle_is_rejected(tmp_path, relative):
    copied = tmp_path / "corpus"
    shutil.copytree(CORPUS, copied)
    path = copied / relative
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="integrity mismatch"):
        verify_development_corpus(copied)


def test_manifest_cannot_read_an_outside_path(tmp_path):
    copied = tmp_path / "corpus"
    shutil.copytree(CORPUS, copied)
    manifest = json.loads((copied / "manifest.json").read_text())
    manifest["cases"][0]["input_file"] = "../outside.json"
    (copied / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="inside its corpus"):
        verify_development_corpus(copied)


def test_independent_arithmetic_uses_compounding_and_percentage_points():
    assert fixture_facts(load_agent_input(CORPUS, "DEV-FORMULA-01"))["compound_return"] == "-0.0100"
    assert fixture_facts(load_agent_input(CORPUS, "DEV-REPORT-01"))["report_minus_result"] == "0.04"


def test_availability_uses_timezone_aware_instant_not_observation_date():
    payload = load_agent_input(CORPUS, "DEV-PIT-01")
    assert fixture_facts(payload)["available_after_decision"] is True
    payload["evidence"]["timing"]["decision_at"] = "2024-01-03T00:00:00Z"
    assert fixture_facts(payload)["available_after_decision"] is False
    payload["evidence"]["timing"]["decision_at"] = "2024-01-03T00:00:00"
    with pytest.raises(ValueError, match="timezone"):
        fixture_facts(payload)


def test_clean_missing_and_false_allegation_controls():
    assert fixture_facts(load_agent_input(CORPUS, "DEV-CLEAN-01"))["missing_evidence_ids"] == []
    assert fixture_facts(load_agent_input(CORPUS, "DEV-MISSING-01"))["missing_evidence_ids"] == ["timing"]
    assert fixture_facts(load_agent_input(CORPUS, "DEV-DISPUTE-01"))["fees_included"] is True


def test_protocol_cases_are_declared_inputs_not_claimed_runtime_enforcement():
    scenarios = json.loads((CORPUS / "protocol-scenarios.json").read_text())
    assert "later phases" in scenarios["status"]
    assert {row["id"] for row in scenarios["scenarios"]} >= {
        "stale_reference", "malformed_output", "duplicate_delivery", "cancellation", "timeout", "exhausted_budget",
    }


def example():
    return json.loads(EXAMPLE.read_text())


def test_example_record_represents_unknown_tokens_without_scoring_quality():
    record = example()
    validate_run_record(record)
    assert record["outcome"]["qualified"] is None
    assert record["totals"]["usage"]["input_tokens"] is None


@pytest.mark.parametrize("status", ["failed", "blocked", "cancelled", "timeout", "invalid_configuration", "skipped"])
def test_unsuccessful_attempts_and_unknown_human_time_remain_representable(status):
    record = example()
    record["outcome"].update(status=status, reason="TEST_FAILURE_RETAINED")
    record["human_interventions"] = [{"kind": "manual_repair", "at": record["finished_at"],
                                      "reason": "Example intervention", "active_seconds": None}]
    validate_run_record(record)


@pytest.mark.parametrize("mutation", [
    lambda r: r["totals"].update(model_calls=99),
    lambda r: r["totals"]["usage"].update(input_tokens=0),
    lambda r: r["totals"]["cost"].update(status="unavailable"),
    lambda r: r["attempts"].append(copy.deepcopy(r["attempts"][0])),
    lambda r: r["timings"].update(total_seconds=float("nan")),
    lambda r: r.update(finished_at="2024-01-01T00:00:00Z"),
    lambda r: r.update(mode="live"),
    lambda r: r.update(unexpected_field=True),
])
def test_invalid_or_misleading_records_are_rejected(mutation):
    record = example()
    mutation(record)
    with pytest.raises((ValueError, ValidationError)):
        validate_run_record(record)


def test_two_roles_include_review_overhead_and_cannot_hide_unknown_cost():
    record = example()
    record["arm"] = "two_agent"
    reviewer = copy.deepcopy(record["roles"][0])
    reviewer["role"] = "reviewer"
    record["roles"].append(reviewer)
    record["totals"]["model_calls"] *= 2
    record["totals"]["tool_calls"] *= 2
    record["attempts"].append({**record["attempts"][0], "id": "review-call-1", "role": "reviewer"})
    validate_run_record(record)
    reviewer["cost"] = {"status": "unavailable", "amount": None, "currency": None,
                        "pricing_snapshot": None, "reason": "Provider billed usage unavailable"}
    with pytest.raises(ValueError, match="unknown"):
        validate_run_record(record)
    record["totals"]["cost"] = copy.deepcopy(reviewer["cost"])
    validate_run_record(record)
