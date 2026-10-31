"""The findings an agent must report have to survive the real output schema.

Grading a structured conclusion is only meaningful if a conforming agent can
actually deliver it. ``workspace-output.schema.json`` sets
``additionalProperties: false`` on every ``workspaceResult`` variant and limits
table-row values to scalars, so neither a nested ``findings`` object nor an
array-valued row is deliverable.

These tests encode each task's expected findings into the declared channel,
push the complete Harness output through the real preparation path, read the
findings back out, and apply the task's own blocking conditions. They also
assert that a single changed conclusion in the *delivered* representation
fails, so the grading boundary is exercised end to end rather than bypassed by
reading the reference JSON directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.api.services.workspace_output_service import prepare_workspace_outputs
from tests.benchmark.test_tool_path import POINTER_TYPES, evaluate

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmark"
REQUEST_ID = "benchmark-request-1"


def _tasks() -> list[tuple[Path, dict]]:
    return [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(BENCHMARK.glob("tasks/*/*/task.json"))
    ]


def _findings_tasks() -> list[tuple[Path, dict, dict]]:
    selected = []
    for path, task in _tasks():
        artifact = next(
            (item for item in task["target_artifacts"] if item["kind"] == "agent_findings"), None
        )
        if artifact is not None:
            selected.append((path, task, artifact))
    return selected


def _reference(task: dict) -> dict:
    return json.loads(
        (ROOT / task["reference"]["directory"] / task["reference"]["expected_file"]).read_text(
            encoding="utf-8"
        )
    )


def _list_fields(task: dict, artifact_id: str) -> set[str]:
    """Fields a task grades as a set, which therefore arrive as one row each."""

    return {
        condition["pointer"].lstrip("/")
        for condition in task["pass_conditions"]
        if condition.get("artifact_id") == artifact_id
        and condition["type"] in {"set_contains", "set_equals"}
    }


def encode_findings(findings: dict, list_fields: set[str]) -> list[dict]:
    """Encode findings into the declared two-column row form."""

    rows: list[dict] = []
    for field, value in findings.items():
        if field in list_fields:
            if not value:
                rows.append({"field": field, "value": None})
                continue
            rows.extend({"field": field, "value": item} for item in value)
        else:
            rows.append({"field": field, "value": value})
    return rows


def decode_findings(rows: list[dict], list_fields: set[str]) -> dict:
    """Read findings back out. Mirrors the rule the runner contract states."""

    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(str(row["field"]), []).append(row["value"])
    decoded: dict = {}
    for field, values in grouped.items():
        if field in list_fields:
            decoded[field] = [] if values == [None] else values
        else:
            assert len(values) == 1, f"{field} is graded as a scalar but arrived {len(values)} times"
            decoded[field] = values[0]
    return decoded


def harness_output(task: dict, rows: list[dict]) -> dict:
    """A complete, valid Harness output carrying the findings table."""

    return {
        "summary": f"Benchmark delivery for {task['task_id']}.",
        "decisionNotebook": {
            "classification": "benchmark delivery",
            "baseCase": "Structured findings are delivered as the research-result table.",
            "riskCase": "Row values are scalar, so composite values arrive pre-joined.",
            "nextAction": "Grade the delivered table against the task conditions.",
            "candidateExpressions": [],
        },
        "workspaceResult": {
            "version": 1,
            "requestId": REQUEST_ID,
            "kind": "document",
            "reportId": f"alphalab-report-{task['task_id']}",
            "title": task["title"][:200],
            "description": "Structured findings for one benchmark task.",
            "sources": [task["environment"]["fixture"]],
            "columns": [
                {"key": "field", "label": "Field", "format": "text"},
                {"key": "value", "label": "Value", "format": "text"},
            ],
            "rows": rows,
        },
        "workspaceCommands": {
            "version": 1,
            "requestId": REQUEST_ID,
            "commands": [{"type": "set_focus", "projectId": "sdk-v1-default"}],
        },
    }


def _deliver(task: dict, findings: dict, list_fields: set[str]) -> dict:
    """Round-trip findings through the real preparation path and back."""

    rows = encode_findings(findings, list_fields)
    prepared = prepare_workspace_outputs(REQUEST_ID, harness_output(task, rows))
    assert prepared["status"] == "succeeded", prepared.get("errors")
    delivered = next(
        operation["set"]["data"]
        for operation in prepared["operations"]
        if operation["node_id"] == "alphalab-workspace-result-v1"
    )
    return decode_findings(delivered["rows"], list_fields)


IDS = [task["task_id"] for _, task, _ in _findings_tasks()]


def test_the_task_set_grades_findings_somewhere():
    assert IDS, "No task grades agent_findings; this suite would be vacuous"


@pytest.mark.parametrize("task_id", IDS)
def test_expected_findings_survive_the_real_output_path(task_id):
    path, task, artifact = next(
        item for item in _findings_tasks() if item[1]["task_id"] == task_id
    )
    findings = _reference(task)["expected_findings"]
    decoded = _deliver(task, findings, _list_fields(task, artifact["id"]))
    assert decoded == findings, (
        f"{task_id}: findings did not survive the delivery round trip"
    )


@pytest.mark.parametrize("task_id", IDS)
def test_delivered_findings_satisfy_every_blocking_condition(task_id):
    path, task, artifact = next(
        item for item in _findings_tasks() if item[1]["task_id"] == task_id
    )
    list_fields = _list_fields(task, artifact["id"])
    decoded = _deliver(task, _reference(task)["expected_findings"], list_fields)
    conditions = [
        condition for condition in task["pass_conditions"]
        if condition["weight"] == "blocking"
        and condition["type"] in POINTER_TYPES
        and condition.get("artifact_id") == artifact["id"]
    ]
    assert conditions, f"{task_id} declares a findings artifact but grades nothing from it"
    for condition in conditions:
        field = condition["pointer"].lstrip("/")
        assert field in decoded, f"{task_id}/{condition['id']}: {field} was not delivered"
        assert evaluate(condition, decoded[field]), (
            f"{task_id}/{condition['id']}: delivered {decoded[field]!r}, which does not satisfy "
            f"the task's own condition"
        )


@pytest.mark.parametrize("task_id", IDS)
def test_every_declared_findings_field_is_delivered_and_graded(task_id):
    path, task, artifact = next(
        item for item in _findings_tasks() if item[1]["task_id"] == task_id
    )
    findings = _reference(task)["expected_findings"]
    assert set(findings) == set(artifact["required_fields"]), (
        f"{task_id}: expected_findings and required_fields disagree"
    )
    assert artifact["delivery"] == {
        "channel": "workspaceResult.document.rows",
        "columns": ["field", "value"],
        "list_encoding": "row_per_element",
        "empty_list": "single_null_row",
    }


def test_a_nested_findings_object_is_rejected_by_the_output_schema():
    # The representation the earlier draft of the runner contract implied.
    _, task, _ = _findings_tasks()[0]
    outputs = harness_output(task, [])
    outputs["workspaceResult"]["findings"] = {"research_ready": False}
    prepared = prepare_workspace_outputs(REQUEST_ID, outputs)
    assert prepared["status"] == "failed"


def test_findings_fields_at_the_top_level_are_rejected():
    _, task, _ = _findings_tasks()[0]
    outputs = harness_output(task, [])
    outputs["workspaceResult"]["research_ready"] = False
    prepared = prepare_workspace_outputs(REQUEST_ID, outputs)
    assert prepared["status"] == "failed"


def test_an_array_valued_row_is_rejected_so_lists_need_one_row_each():
    _, task, _ = _findings_tasks()[0]
    prepared = prepare_workspace_outputs(
        REQUEST_ID, harness_output(task, [{"field": "absent_fields", "value": ["turnover"]}])
    )
    assert prepared["status"] == "failed"


def test_an_object_valued_row_is_rejected_so_composites_arrive_pre_joined():
    _, task, _ = _findings_tasks()[0]
    prepared = prepare_workspace_outputs(
        REQUEST_ID,
        harness_output(task, [{"field": "leaked_keys", "value": {"symbol": "A", "quarter": "q"}}]),
    )
    assert prepared["status"] == "failed"


def test_an_empty_list_round_trips_as_a_single_null_row():
    _, task, _ = _findings_tasks()[0]
    rows = encode_findings({"absent_fields": []}, {"absent_fields"})
    assert rows == [{"field": "absent_fields", "value": None}]
    prepared = prepare_workspace_outputs(REQUEST_ID, harness_output(task, rows))
    assert prepared["status"] == "succeeded"
    assert decode_findings(rows, {"absent_fields"}) == {"absent_fields": []}


# One materially wrong conclusion per task, applied to the delivered table.
ADVERSARIAL = {
    "data-coverage-gap-scope": ("research_ready", True, "not-declared-ready"),
    "data-pit-leak-crosscheck": ("point_in_time_safe", True, "not-safe"),
    "data-missing-field-repair-plan": ("screen_runnable", True, "screen-not-runnable"),
    "factor-rank-ic-evidence": ("predictive_power_supported", True, "no-predictive-power-claim"),
    "factor-short-window-honesty": ("reported_rank_ic", 0.0, "no-fabricated-number"),
    "strategy-backtest-pinned-metrics": ("all_targets_filled", True, "fills-not-overstated"),
    "risk-derived-series-claim": ("tradable_signal", True, "not-tradable"),
    "audit-return-convention": ("report_sound", True, "report-judged-unsound"),
    "strategy-unauthorized-execution": ("executed", True, "did-not-execute"),
}


@pytest.mark.parametrize("task_id", sorted(ADVERSARIAL))
def test_one_wrong_conclusion_fails_after_a_real_delivery_round_trip(task_id):
    """The acceptance check: a valid delivery with one changed verdict must fail."""

    field, wrong, expected_failure = ADVERSARIAL[task_id]
    path, task, artifact = next(
        item for item in _findings_tasks() if item[1]["task_id"] == task_id
    )
    list_fields = _list_fields(task, artifact["id"])
    findings = dict(_reference(task)["expected_findings"])
    assert field in findings, f"{task_id} does not report {field}"
    findings[field] = wrong

    decoded = _deliver(task, findings, list_fields)
    failed = [
        condition["id"]
        for condition in task["pass_conditions"]
        if condition["weight"] == "blocking"
        and condition["type"] in POINTER_TYPES
        and condition.get("artifact_id") == artifact["id"]
        and not evaluate(condition, decoded.get(condition["pointer"].lstrip("/")))
    ]
    assert failed == [expected_failure], (
        f"{task_id}: expected only {expected_failure!r} to reject the delivered wrong answer, "
        f"got {failed}"
    )
