"""Contract tests that drive the real Conexus tool, not just the Python SDK.

The task set is only meaningful if a conforming agent can satisfy it through the
interface it is actually given. These tests execute
``integrations/conexus/.../Project-Run.tool.json`` under node against mocked API
responses, then resolve every blocking pointer in every task against the
envelope the tool really returns.

They also pin the two interface limits that shaped the task set: Project-Run
hard-codes ``profile: "runtime"`` and never forwards ``execution_data_policy``.
If either changes, the tasks that were scoped around them can be revisited.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmark"
TOOL = ROOT / "integrations" / "conexus" / "alphalab-research-agent" / "tools" / "Project-Run.tool.json"

# Every API response below is recorded from production, never written by hand.
# The earlier hand-written summary invented return_periods_per_year and a nested
# diagnostics wrapper, neither of which build_backtest_summary returns, and the
# mock hid that from the contract tests. Refresh with
# python -m scripts.record_benchmark_references; --check fails on drift.
API_RESPONSES = json.loads(
    Path(__file__).with_name("production_payloads.json").read_text(encoding="utf-8")
)

# The commands the task set collects artifacts from, with arguments a task would send.
INVOCATIONS = {
    "data.validate": {"command": "data.validate",
                       "args": {"dataset": "rq.bars", "fail_on_gap": True, "as_of": "2025-05-07"}},
    "data.query": {"command": "data.query",
                    "args": {"dataset": "rq.daily_factors", "columns": ["date", "symbol", "field"], "limit": 50}},
    "factor.research": {"command": "factor.research", "confirm_python_execution": True,
                         "args": {"project_id": "p1", "factor_id": "momentum_20d",
                                   "start_date": "2025-02-03", "end_date": "2027-12-01",
                                   "quantiles": 5, "horizons": [1, 3, 6]}},
    "backtest.summary": {"command": "backtest.summary", "args": {"backtest_id": "bt-1"}},
    "backtest.robustness": {"command": "backtest.robustness", "args": {"backtest_id": "bt-1"}},
    "market_risk.evaluate": {"command": "market_risk.evaluate",
                              "args": {"name": "defensive_tilt", "expression": "MKT - 0.5 * SMB",
                                        "start_date": "2025-02-03", "end_date": "2027-12-01"}},
    # data.validate serves three fixtures with different planted defects. The
    # mocked report is selected by the start argument, which acts purely as a
    # variant marker here.
    "data.validate@stale": {"command": "data.validate",
                             "args": {"dataset": "rq.bars", "fail_on_gap": True,
                                       "as_of": "2025-05-07", "start": "2025-01-02"}},
    "data.validate@duplicates": {"command": "data.validate",
                                  "args": {"dataset": "rq.bars", "start": "2025-01-03"}},
    # factor.research serves two tasks with opposite expectations, so it is driven
    # twice: once over a long window and once over a window too short to estimate.
    "factor.research@insufficient": {"command": "factor.research", "confirm_python_execution": True,
                                      "args": {"project_id": "p1", "factor_id": "momentum_20d",
                                                "start_date": "2025-02-03", "end_date": "2025-05-07",
                                                "quantiles": 5, "horizons": [1, 3, 6]}},
}

# Which driven variant a task's artifacts resolve against, when a command has more
# than one representative response.
VARIANTS = {
    "factor-short-window-honesty": "factor.research@insufficient",
    "recovery-stale-data-retry": "data.validate@stale",
    "data-duplicate-keys-audit": "data.validate@duplicates",
    "recovery-duplicate-run-guard": "data.validate@duplicates",
}

_DRIVER = r"""
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { bindToolToInstance } from './scripts/lib/conexus-instance.mjs'

const ORIGIN = 'http://127.0.0.1:18003'
const tool = JSON.parse(readFileSync(process.argv[1], 'utf8'))
const responses = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const invocations = JSON.parse(readFileSync(process.argv[3], 'utf8'))
let lastResearchBody = null
let lastValidateBody = null

function route(path) {
  const clean = path.split('?')[0]
  if (clean.endsWith('/identity')) return { instance_id: 'dev3' }
  if (clean.includes('data.validate')) {
    const start = lastValidateBody && lastValidateBody.start
    const report = start === '2025-01-02' ? responses.validate_stale
      : start === '2025-01-03' ? responses.validate_duplicates : responses.validate
    return { tool: 'data.validate', mutating: false, result: report }
  }
  if (clean.includes('data.query')) return { tool: 'data.query', mutating: false, result: responses.query }
  if (clean.endsWith('/research')) return lastResearchBody && lastResearchBody.end_date === '2025-05-07'
    ? responses.research_insufficient : responses.research
  if (clean.endsWith('/summary')) return responses.summary
  if (clean.endsWith('/robustness')) return responses.robustness
  if (clean.includes('custom-risk-factor')) return responses.market_risk
  if (/\/api\/strategy\/projects\/[^/]+$/.test(clean)) return { id: 'p1', dirty: false, current_revision: 3 }
  return null
}

function clean_research_probe(path) { return path.split('?')[0].endsWith('/research') }

const out = {}
for (const [label, input] of Object.entries(invocations)) {
  lastResearchBody = null
  lastValidateBody = null
  const bound = bindToolToInstance(tool, ORIGIN, 'dev3')
  const requests = []
  const context = vm.createContext({
    AbortSignal,
    process: { env: { ALPHALAB_API_ORIGIN: ORIGIN } },
    fetch: async (url, init) => {
      const path = String(url).replace(ORIGIN, '')
      if (!path.includes('/identity'))
        requests.push({ path, body: init && init.body ? JSON.parse(init.body) : null })
      if (clean_research_probe(path) && init && init.body) lastResearchBody = JSON.parse(init.body)
      if (path.includes('data.validate') && init && init.body) lastValidateBody = JSON.parse(init.body).input
      const body = route(path)
      if (body === null) return { ok: false, status: 404, json: async () => ({ detail: 'no route' }), text: async () => 'no route' }
      return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) }
    },
  })
  vm.runInContext(bound.code, context)
  const result = await vm.runInContext(`run(${JSON.stringify(input)})`, context)
  out[label] = { requests, envelope: result }
}
console.log(JSON.stringify(out))
"""


def _drive_tool(tmp_path: Path) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute the Conexus tool")
    responses = tmp_path / "responses.json"
    responses.write_text(json.dumps(API_RESPONSES), encoding="utf-8")
    invocations = tmp_path / "invocations.json"
    invocations.write_text(json.dumps(INVOCATIONS), encoding="utf-8")
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", _DRIVER,
         str(TOOL), str(responses), str(invocations)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _tasks() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(BENCHMARK.glob("tasks/*/*/task.json"))
    ]


def _reference(task: dict) -> dict:
    path = ROOT / task["reference"]["directory"] / task["reference"]["expected_file"]
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(document, pointer: str):
    """Resolve a JSON Pointer, returning a sentinel when a token is absent."""

    missing = object()
    current = document
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return missing
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or int(token) >= len(current):
                return missing
            current = current[int(token)]
        else:
            return missing
    return current


MISSING = _resolve({}, "/absent")


def _project(rows, fields: list[str]) -> set[str]:
    return {":".join(str(row[field]) for field in fields) for row in rows}


def evaluate(condition: dict, value) -> bool:
    """Reference semantics for the machine-checkable condition types."""

    kind = condition["type"]
    expect = condition.get("expect")
    if value is MISSING:
        return False
    if kind == "numeric":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return abs(float(value) - float(expect)) <= float(condition.get("tolerance", 0.0))
    if kind == "scalar_equals":
        if isinstance(expect, bool) or expect is None:
            return value is expect
        return value == expect
    if kind in {"set_contains", "set_equals"}:
        if not isinstance(value, list):
            return False
        actual = _project(value, condition["project"]) if condition.get("project") else {
            str(item) for item in value
        }
        wanted = {str(item) for item in expect}
        return wanted <= actual if kind == "set_contains" else wanted == actual
    raise AssertionError(f"evaluate() does not handle {kind}")


POINTER_TYPES = {"numeric", "scalar_equals", "set_contains", "set_equals"}


def test_data_validate_forwards_the_fields_its_registry_accepts(tmp_path):
    # DatasetInput declares dataset, start, as_of and fail_on_gap. Without the
    # forwarding, every diagnostic fixture validates as "passed" and the
    # coverage tasks become unsatisfiable.
    driven = _drive_tool(tmp_path)
    body = driven["data.validate"]["requests"][0]["body"]["input"]
    assert body["dataset"] == "rq.bars"
    assert body["fail_on_gap"] is True
    assert body["as_of"] == "2025-05-07"
    assert "required_fields" not in body, (
        "required_fields is not part of the data-tool registry input; no task may depend on it"
    )


def test_the_tool_still_pins_the_runtime_profile_and_withholds_execution_policy(tmp_path):
    # Both limits are why no task uses the demo bundle or selects a policy.
    driven = _drive_tool(tmp_path)
    research = driven["factor.research"]["requests"][-1]["body"]
    assert research["profile"] == "runtime"
    assert "execution_data_policy" not in research
    risk = driven["market_risk.evaluate"]["requests"][-1]["body"]
    assert risk["profile"] == "runtime"


def test_the_validation_report_status_arrives_as_data_status(tmp_path):
    # The envelope's own status reports tool success, so the report's status is
    # renamed. A pointer at /status would silently read "succeeded".
    driven = _drive_tool(tmp_path)
    envelope = driven["data.validate"]["envelope"]
    assert envelope["status"] == "succeeded"
    assert envelope["data_status"] == "failed"
    assert [issue["code"] for issue in envelope["issues"]] == ["bar_key_gap"]


def test_factor_research_nests_its_payload_and_strips_internal_fields(tmp_path):
    driven = _drive_tool(tmp_path)
    envelope = driven["factor.research"]["envelope"]
    assert envelope["status"] == "succeeded"
    assert envelope["result"]["status"] == "sufficient"
    assert envelope["result"]["periods"] == 34
    for stripped in ("source_sha256", "stdout", "stderr"):
        assert stripped not in envelope["result"], (
            f"{stripped} is removed by the tool, so no reference may claim the agent observes it"
        )
    # Long arrays are summarised rather than passed through. rows carries one
    # entry per evaluated period, so it is the array this payload actually has;
    # an earlier draft asserted a universe key the real response never returns.
    assert envelope["result"]["rows"]["count"] == 34
    assert envelope["result"]["rows"]["truncated"] is True
    # Short arrays survive intact, which is why /result/warnings stays a list.
    assert isinstance(envelope["result"]["warnings"], list)


def test_every_blocking_tool_pointer_resolves_against_a_real_envelope(tmp_path):
    driven = _drive_tool(tmp_path)
    checked = 0
    for task in _tasks():
        artifacts = {item["id"]: item for item in task["target_artifacts"]}
        for condition in task["pass_conditions"]:
            if condition["weight"] != "blocking" or condition["type"] not in POINTER_TYPES:
                continue
            artifact = artifacts[condition["artifact_id"]]
            if artifact["kind"] not in {"tool_result", "backtest_run"}:
                continue
            command = VARIANTS.get(task["task_id"], artifact["source_command"])
            assert command in driven, f"{task['task_id']} collects from un-exercised {command}"
            envelope = driven[command]["envelope"]
            value = _resolve(envelope, condition["pointer"])
            assert value is not MISSING, (
                f"{task['task_id']}/{condition['id']}: {condition['pointer']} does not resolve "
                f"against the {command} envelope"
            )
            assert evaluate(condition, value), (
                f"{task['task_id']}/{condition['id']}: {condition['pointer']} resolved to "
                f"{value!r}, which does not satisfy the recorded expectation"
            )
            checked += 1
    assert checked >= 10, f"Only {checked} blocking tool pointers were exercised"


def test_declared_artifact_required_fields_are_present_in_the_envelope(tmp_path):
    driven = _drive_tool(tmp_path)
    for task in _tasks():
        for artifact in task["target_artifacts"]:
            if artifact["kind"] not in {"tool_result", "backtest_run"}:
                continue
            command = VARIANTS.get(task["task_id"], artifact["source_command"])
            envelope = driven[command]["envelope"]
            for field in artifact.get("required_fields", []):
                assert field in envelope, (
                    f"{task['task_id']}/{artifact['id']} requires {field!r}, absent from the "
                    f"{command} envelope"
                )


def test_recorded_findings_satisfy_every_blocking_findings_condition():
    # The reference's expected_findings stands in for a correct agent's output.
    covered = 0
    for task in _tasks():
        reference = _reference(task)
        findings = reference.get("expected_findings")
        conditions = [
            item for item in task["pass_conditions"]
            if item["weight"] == "blocking"
            and item["type"] in POINTER_TYPES
            and next(a for a in task["target_artifacts"] if a["id"] == item["artifact_id"])["kind"]
            == "agent_findings"
        ]
        if not conditions:
            continue
        assert findings is not None, (
            f"{task['task_id']} grades agent_findings but its reference records no expected_findings"
        )
        for condition in conditions:
            value = _resolve(findings, condition["pointer"])
            assert value is not MISSING, (
                f"{task['task_id']}/{condition['id']}: {condition['pointer']} absent from "
                f"expected_findings"
            )
            assert evaluate(condition, value), (
                f"{task['task_id']}/{condition['id']}: expected_findings holds {value!r}, which "
                f"does not satisfy the task's own condition"
            )
            covered += 1
    assert covered >= 15, f"Only {covered} blocking findings conditions were covered"


def test_every_task_grading_findings_asks_the_agent_for_them():
    # A structured conclusion can only be required if the prompt requests it.
    for path in sorted(BENCHMARK.glob("tasks/*/*/task.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        findings = [a for a in task["target_artifacts"] if a["kind"] == "agent_findings"]
        if not findings:
            continue
        prompt = (path.parent / task["request"]["prompt_file"]).read_text(encoding="utf-8")
        for field in findings[0]["required_fields"]:
            assert field in prompt, (
                f"{task['task_id']} grades {field!r} but never asks the agent for it"
            )

# Adversarial cases live in test_findings_delivery.py, which applies them to a
# findings table that has been through the real output-preparation path. Testing
# them here as well would duplicate the list against a weaker boundary.
