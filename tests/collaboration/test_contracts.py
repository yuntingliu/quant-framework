from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from alphalab.collaboration import (
    CollaborationError,
    decode_json,
    validate_contract,
    validate_contract_shape,
)
from apps.api.services.workspace_output_service import prepare_workspace_outputs
from scripts.check_collaboration_contracts import load_contract_cases

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/collaboration/contracts"
CASES = load_contract_cases(FIXTURES)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_shared_contract_cases(case):
    if case["expected_code"]:
        with pytest.raises(CollaborationError) as caught:
            validate_contract(case["kind"], case["payload"], case["context"])
        assert caught.value.code == case["expected_code"]
        assert "do-not-echo" not in str(caught.value)
    else:
        validate_contract(case["kind"], case["payload"], case["context"])


@pytest.mark.skipif(not (ROOT / "build/conexus/node_modules/ajv").exists() or not shutil.which("node"), reason="Build the bundled Node runtime first.")
def test_node_python_accept_reject_and_error_codes_match():
    completed = subprocess.run([sys.executable, str(ROOT / "scripts/check_collaboration_contracts.py"), "--cross-language"],
                               cwd=ROOT, capture_output=True, text=True, timeout=40, check=True)
    assert json.loads(completed.stdout)["node"] == "passed"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_values_cannot_enter_a_research_package(value):
    case = copy.deepcopy(next(case for case in CASES if case["name"] == "valid-research_package"))
    case["payload"]["metrics"][0]["value"] = value
    with pytest.raises(CollaborationError, match="versioned contract"):
        validate_contract(case["kind"], case["payload"], case["context"])


def test_old_output_service_stays_compatible_with_the_wrapped_four_fields():
    example = json.loads((FIXTURES / "examples.json").read_text())
    request = example["messages"]["final_output_request"]
    assert set(request["outputs"]) == {"summary", "decisionNotebook", "workspaceResult", "workspaceCommands"}
    prepared = prepare_workspace_outputs(request["request_id"], request["outputs"])
    assert prepared["status"] == "succeeded"
    assert len(prepared["operations"]) == 3


def test_whole_report_and_output_bundle_are_part_of_reviewed_identity():
    example = json.loads((FIXTURES / "examples.json").read_text())
    final = example["messages"]["final_output_request"]
    assert final["payload_sha256"] == example["context"]["evidence"]["publication"]["sha256"]
    assert example["context"]["evidence"]["publication"]["content"]["report"] == final["draft_report"]


def test_duplicate_json_keys_and_excessive_nesting_are_rejected():
    with pytest.raises(CollaborationError):
        decode_json(b'{"status":"pass","status":"blocked"}')
    with pytest.raises(CollaborationError):
        decode_json(b'{"value":1e400}')
    value = {}
    for _ in range(70):
        value = {"nested": value}
    with pytest.raises(CollaborationError):
        validate_contract_shape("task_envelope", value)


def test_contract_without_trusted_context_is_not_accepted():
    package = json.loads((FIXTURES / "examples.json").read_text())["messages"]["research_package"]
    validate_contract_shape("research_package", package)
    with pytest.raises(CollaborationError) as error:
        validate_contract("research_package", package)
    assert error.value.code == "MISSING_CONTEXT"
