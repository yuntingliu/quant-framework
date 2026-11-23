from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from apps.api.services.comparison.benchmark_evaluation import evaluate_records
from apps.api.services.comparison.evaluation_input import (
    bind_observation,
    committed_workspace_artifacts,
    validate_trial_for_plan,
)
from apps.api.services.comparison.evaluation_protocol import (
    freeze_protocol,
    read_json,
    sha256_json,
    verify_protocol,
)
from apps.api.services.workspace_output_service import prepare_workspace_outputs
from scripts.evaluate_agent_benchmark import main

_ROOT = Path(__file__).resolve().parents[2]
_DEMO = _ROOT / "benchmark" / "evaluation"
_SUITE = _ROOT / "tests" / "fixtures" / "agent_benchmark"


def _fixture(filename):
    return read_json(_DEMO / filename)


def _plan():
    return _fixture("demo_plan.json")


def test_cli_replays_seeded_bad_answers_and_retains_all_outcomes(tmp_path, monkeypatch):
    def prohibit_connection(*args, **kwargs):
        pytest.fail("Offline benchmark replay attempted a network connection")

    monkeypatch.setattr("socket.create_connection", prohibit_connection)
    monkeypatch.setattr("socket.socket.connect", prohibit_connection)
    lock = tmp_path / "lock.json"
    report = tmp_path / "report.json"
    candidate = tmp_path / "candidate.json"
    diff = tmp_path / "diff.json"
    suite = ["--suite-root", str(_SUITE)]
    assert main([*suite, "freeze", "--plan", str(_DEMO / "demo_plan.json"), "--out", str(lock)]) == 0
    assert main([
        *suite, "evaluate", "--lock", str(lock), "--records", str(_DEMO / "demo_records.json"),
        "--observations", str(_DEMO / "demo_observations.json"), "--out", str(report),
        "--regression-out", str(candidate), "--system-version", "issue9-demo",
    ]) == 0
    result = read_json(report)
    outcomes = result["trial_outcomes"]
    assert result["protocol"]["task_set"] == "issue9-offline-fixtures"
    assert len(outcomes) == result["protocol"]["expected_trials"] == 8
    assert sum(row["qualified"] for row in outcomes) == 5
    assert sum(row["infrastructure_failure"] for row in outcomes) == 1
    assert sum(row["safety"]["unauthorized_actions"] for row in outcomes) == 1
    assert any(row["qualified"] and row["outcome"] == "refused" for row in outcomes)
    pit_miss = next(row for row in result["grading"] if row["trial_id"].startswith("demo-B-1-data"))
    assert any(check["id"] == "leaked-rows-exact" and check["status"] == "failed" for check in pit_miss["checks"])
    assert result["protocol"]["claim_status"] == "development_evidence_only"
    assert main([
        "regress", "--baseline", str(_DEMO / "demo_regression_baseline.json"),
        "--candidate", str(candidate), "--out", str(diff),
    ]) == 0
    assert read_json(diff)["unchanged_cases"] == 8


def test_neutral_record_requires_evidence_beyond_runner_completion():
    trial = _fixture("demo_records.json")[0]
    trial["results"]["outputs"]["prepared"] = "private raw output marker"
    lock, _ = freeze_protocol(_plan(), _SUITE)
    report, snapshot = evaluate_records(lock, _SUITE, [trial])
    assert snapshot is None
    assert len(report["protocol"]["missing_trials"]) == 7
    assert len(report["trial_outcomes"]) == 1
    assert report["trial_outcomes"][0]["runtime_status"] == "completed"
    assert report["trial_outcomes"][0]["qualified"] is False
    assert "private raw output marker" not in json.dumps(report)


def test_freeze_rejects_tampering_wrong_split_and_multivariate_ablation():
    plan = _plan()
    lock, _ = freeze_protocol(plan, _SUITE)
    tampered = deepcopy(lock)
    tampered["plan"]["thresholds"]["quality_floor"] = 0.4
    with pytest.raises(ValueError, match="Protocol lock"):
        verify_protocol(tampered, _SUITE)
    plan["split"] = "holdout"
    with pytest.raises(ValueError, match="specified split"):
        freeze_protocol(plan, _SUITE)
    plan = _plan()
    plan["comparisons"][0].update(kind="ablation", feature="skills")
    plan["configurations"]["C"]["features"]["financial_tools"] = False
    plan["configurations"]["C"]["features"]["skills"] = False
    with pytest.raises(ValueError, match="exactly"):
        freeze_protocol(plan, _SUITE)


def test_cli_explains_missing_task_suite(tmp_path, capsys):
    lock = tmp_path / "lock.json"
    assert main([
        "--suite-root", str(tmp_path), "freeze", "--plan", str(_DEMO / "demo_plan.json"),
        "--out", str(lock),
    ]) == 2
    error = capsys.readouterr().err
    assert "Missing benchmark/manifest.json" in error
    assert "--suite-root" in error
    assert not lock.exists()


@pytest.mark.parametrize(("relative_path", "error"), [
    ("tasks/dev/data-pit-leak-crosscheck/prompt.md", "Protocol lock or frozen input changed"),
    ("data/demo-evidence/evidence.json", "Frozen fixture differs from its manifest"),
])
def test_external_suite_cannot_change_after_freeze(tmp_path, relative_path, error):
    suite = tmp_path / "suite"
    shutil.copytree(_SUITE, suite)
    lock, _ = freeze_protocol(_plan(), suite)
    pinned = suite / "benchmark" / relative_path
    pinned.write_bytes(pinned.read_bytes() + b"\n")
    with pytest.raises(ValueError, match=error):
        verify_protocol(lock, suite)


def test_observations_do_not_overwrite_trial_status_or_cross_record_boundaries():
    record, another = _fixture("demo_records.json")[:2]
    evidence = _fixture("demo_observations.json")[record["trial_id"]]
    with pytest.raises(ValueError, match="does not match"):
        bind_observation(another, evidence)
    forged = {"record_sha256": sha256_json(record), "observed": {"execution": {"status": "completed"}}}
    with pytest.raises(ValueError, match="Unknown observation field"):
        bind_observation(record, forged)
    record["initial_state"]["initial_data_hash"] = "<redacted>"
    fingerprint = "a" * 64
    captured = {
        "record_sha256": sha256_json(record),
        "observed": {"initial_state": {"initial_data_hash": fingerprint}},
    }
    assert bind_observation(record, captured)["initial_state"]["initial_data_hash"] == fingerprint
    record["initial_state"]["initial_data_hash"] = "b" * 64
    captured["record_sha256"] = sha256_json(record)
    with pytest.raises(ValueError, match="conflicts"):
        bind_observation(record, captured)


def test_only_valid_current_run_workspace_artifacts_are_collected():
    _, tasks = freeze_protocol(_plan(), _SUITE)
    task = tasks["data-pit-leak-crosscheck"].task
    outputs = {
        "summary": "Reviewed the fixture.",
        "decisionNotebook": {
            "classification": "reviewed", "baseCase": "Current evidence.",
            "riskCase": "PIT leakage.", "nextAction": "Withhold approval.", "candidateExpressions": [],
        },
        "workspaceResult": {
            "version": 1, "requestId": "request-1", "kind": "document", "reportId": "report-1",
            "title": "Current report", "sources": [],
            "columns": [{"key": "field", "label": "Field"}, {"key": "value", "label": "Value"}],
            "rows": [{"field": "point_in_time_safe", "value": False}],
        },
        "workspaceCommands": {"version": 1, "requestId": "request-1", "commands": []},
    }
    staged = prepare_workspace_outputs("request-1", outputs)
    assert staged["status"] == "succeeded"
    assert committed_workspace_artifacts(task, {"id": "run-1"}, {"nodes": []}) == {}
    nodes = [
        {"id": op["node_id"], "updatedByRunId": "run-1", "values": op["set"]}
        for op in staged["operations"]
    ]
    run = {"id": "run-1", "nodeChanges": {"created": ["report-1"], "updated": [n["id"] for n in nodes]}}
    report = {
        "id": "report-1", "type": "document", "description": "AlphaLab quantitative report",
        "values": {"content": "# Evidence"}, "updatedByRunId": "previous-run",
    }
    nodes.append(report)
    assert committed_workspace_artifacts(task, run, {"nodes": nodes}).keys() == {"notebook"}
    report["updatedByRunId"] = "run-1"
    assert committed_workspace_artifacts(task, run, {"nodes": nodes}).keys() == {"notebook", "findings"}


def test_confirmation_rejects_unregistered_digest_and_keeps_unmatched_failed_trials():
    plan = _plan()
    plan.update(split="holdout", task_ids=["audit-underspecified-request"], source_class="private_replay")
    for config in plan["configurations"].values():
        config.update(
            model_id="deepseek/deepseek-flash", provider="provider-v1", reasoning="off", hardware="cpu",
            permissions_sha256="2" * 64, assets_sha256="3" * 64, search_policy="disabled",
            framework_version="v1",
        )
    lock, tasks = freeze_protocol(plan, _SUITE)
    with pytest.raises(ValueError, match="separately registered"):
        evaluate_records(lock, _SUITE, [])
    task_id = plan["task_ids"][0]
    record = deepcopy(_fixture("demo_records.json")[0])
    record.update(task_id=task_id, task_category="result_audit")
    record["reproducibility"].update(experiment_id=plan["experiment_id"], task_version=lock["inputs"][task_id])
    record["conditions"].update(source="recorded", stage="confirmation", mode="automated")
    record["execution"]["status"] = "failed"
    report, _ = evaluate_records(lock, _SUITE, [record], expected_protocol_sha256=lock["protocol_sha256"])
    assert report["protocol"]["scope"] == "private"
    assert report["trial_outcomes"][0]["qualified"] is False
    assert report["trial_outcomes"][0]["runtime_status"] == "failed"
    assert any(check["id"] == "protocol:observed-controls" and check["status"] == "failed"
               for check in report["grading"][0]["checks"])
    assert report["protocol"]["claim_status"] == "development_evidence_only"

    record["initial_state"]["initial_data_hash"] = tasks[task_id].fixture_sha256
    record["conditions"].update({key: value for key, value in plan["configurations"]["B"].items() if key != "base_config"})
    record["execution"]["observation_complete"] = True
    record["reproducibility"].update(interleaved_order=0, plan_size=1)
    checked = validate_trial_for_plan(record, tasks[task_id], plan, order={(task_id, "B", 0): 1})
    assert checked["conditions"]["conditions_not_matched"] == ["interleaved_order"]


def test_freeze_enforces_the_declared_task_schema(tmp_path):
    suite = tmp_path / "suite"
    shutil.copytree(_SUITE, suite)
    task_file = suite / "benchmark" / "tasks" / "dev" / "data-pit-leak-crosscheck" / "task.json"
    task = read_json(task_file)
    task["expected_outcome"] = "maybe"
    task_file.write_text(json.dumps(task), encoding="utf-8")
    with pytest.raises(ValueError, match="task.schema.json"):
        freeze_protocol(_plan(), suite)


def test_freeze_requires_the_task_schema(tmp_path):
    suite = tmp_path / "suite"
    shutil.copytree(_SUITE, suite)
    (suite / "benchmark" / "schema" / "task.schema.json").unlink()
    with pytest.raises(ValueError, match="task.schema.json"):
        freeze_protocol(_plan(), suite)
