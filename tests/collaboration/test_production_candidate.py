"""Member 2: real evidence freezing and candidate registration, without publication.

Runs offline against a synthetic archived ResultStore export. No market-data,
model, Conexus runtime, or external services are used.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from alphalab.collaboration import EvidenceStore, validate_contract
from alphalab.collaboration.contracts import CollaborationError, sha256
from apps.api.services.agent_collaboration_service import prepare_collaboration_candidate

EXAMPLES = (Path(__file__).resolve().parents[1] / "fixtures" / "collaboration" /
            "contracts" / "examples.json")


class ArchivedResultStore:
    """Small deterministic stand-in for ResultStore.export_backtest_evidence."""

    def __init__(self, *, missing_sources=False, no_returns=False, bad_digest=False):
        strategy = "def initialize(context):\n    pass\n"
        validation = "def validate(context):\n    return True\n"
        self.calls = []
        self.export = {
            "record": {
                "id": "backtest-1", "start_date": "2024-01-02", "end_date": "2024-01-03",
                "total_return": -0.01, "annual_return": -0.72,
                "annual_vol": 0.20, "sharpe": -0.3, "max_drawdown": -0.10,
                "n_periods": 2, "strategy_project_id": "project-1",
                "project_strategy_id": "main", "code_version": "revision-1",
                "strategy_source": None if missing_sources else strategy,
                "strategy_source_sha256": None if missing_sources else (
                    "0" * 64 if bad_digest else sha256(strategy.encode())),
                "strategy_revision": 1,
                "validation_source": None if missing_sources else validation,
                "validation_source_sha256": None if missing_sources else sha256(validation.encode()),
                "validation_revision": 1,
                **{key: None for key in (
                    "execution_json", "event_json", "provenance_json",
                    "validation_output_json", "run_diagnostics_json",
                    "settings_json", "strategy_manifest_json")},
            },
            "returns": [] if no_returns else [
                {"date": "2024-01-02", "strategy": 0.10},
                {"date": "2024-01-03", "strategy": -0.10},
            ],
            "weights": [],
        }

    def export_backtest_evidence(self, backtest_id):
        self.calls.append(backtest_id)
        return copy.deepcopy(self.export) if backtest_id == "backtest-1" else None


@pytest.fixture
def inputs(tmp_path):
    examples = json.loads(EXAMPLES.read_text(encoding="utf-8"))["messages"]
    task = copy.deepcopy(examples["task_envelope"])
    outputs = copy.deepcopy(examples["final_output_request"]["outputs"])
    return {
        "task": task,
        "backtest_id": "backtest-1",
        "request_id": outputs["workspaceResult"]["requestId"],
        "report_markdown": "# Draft research\n\nThe archived total return is reported as -1%.",
        "outputs": outputs,
        "claim_text": "The archived backtest reports a total return of -1%.",
        "evidence_store": EvidenceStore(tmp_path / "evidence"),
        "result_store": ArchivedResultStore(),
    }


def test_registers_real_frozen_candidate_without_publication(inputs):
    result = prepare_collaboration_candidate(**inputs)
    store = inputs["evidence_store"]
    package = result["research_package"]
    context = store.candidate_context(result["task_id"], result["candidate"])
    validate_contract("research_package", package, context)

    assert result["publication_performed"] is False
    assert package["revision"] == result["manifest"]["revision"] == 1
    assert package["manifest"] == result["manifest"]
    assert package["draft_report"]["artifact_id"] == "report"
    assert package["publication"]["artifact_id"] == "publication"
    assert package["claims"][0]["evidence"][0]["locator"] == "/metrics"
    assert {ref["artifact_id"] for ref in package["source_refs"]} == {
        "strategy-source", "validation-source"}
    assert {ref["artifact_id"] for ref in package["data_refs"]} == {
    "return-series"
    }

    assert "position-weights" in {
        ref["artifact_id"] for ref in package["result_refs"]
    }
    assert inputs["result_store"].calls == ["backtest-1"]

    # The proposed publication is archived as evidence, NOT committed.
    assert context["research_package"] == package
    assert result["coverage"]["pit"] == "unavailable"
    assert package["timing"] == {"decision_at": None, "availability_evidence": []}
    assert any("availability" in text for text in package["limitations"])

def test_candidate_reference_kinds(inputs):
    result = prepare_collaboration_candidate(**inputs)

    package = result["research_package"]

    # Retrieve the complete frozen evidence manifest.
    store = inputs["evidence_store"]
    context = store.candidate_context(
        result["task_id"],
        result["candidate"],
    )

    manifest = context["manifest"]

    kinds = {
        artifact["artifact_id"]: artifact["kind"]
        for artifact in manifest["artifacts"]
    }

    allowed = {
        "source_refs": {"source"},
        "data_refs": {"data", "returns", "timing"},
        "result_refs": {
            "result",
            "returns",
            "validation",
            "positions",
            "executions",
        },
    }

    for field, permitted in allowed.items():
        for ref in package[field]:
            assert ref["artifact_id"] in kinds
            assert kinds[ref["artifact_id"]] in permitted

def test_missing_sources_and_returns_are_not_invented(inputs):
    inputs["result_store"] = ArchivedResultStore(missing_sources=True, no_returns=True)
    result = prepare_collaboration_candidate(**inputs)
    package = result["research_package"]
    assert package["source_refs"] == []
    assert package["metrics"] == []
    assert result["coverage"]["sources"] == "unavailable"
    assert result["coverage"]["results"] == "partial"
    assert any("No persisted return rows" in text for text in result["omissions"])
    assert result["publication_performed"] is False


def test_invalid_workspace_outputs_rejected_before_task_registration(inputs):
    inputs["outputs"]["workspaceResult"]["requestId"] = "wrong-request"
    with pytest.raises(CollaborationError) as error:
        prepare_collaboration_candidate(**inputs)
    assert error.value.code == "INVALID_WORKSPACE_OUTPUTS"
    with pytest.raises(CollaborationError) as absent:
        inputs["evidence_store"].task(inputs["task"]["task_id"])
    assert absent.value.code == "ACCESS_DENIED"


def test_tampered_source_digest_rejected_before_registration(inputs):
    inputs["result_store"] = ArchivedResultStore(bad_digest=True)
    with pytest.raises(CollaborationError) as error:
        prepare_collaboration_candidate(**inputs)
    assert error.value.code == "INTEGRITY_ERROR"
    with pytest.raises(CollaborationError):
        inputs["evidence_store"].task(inputs["task"]["task_id"])


def test_initial_candidate_rejects_nonzero_revision(inputs):
    with pytest.raises(CollaborationError) as error:
        prepare_collaboration_candidate(**inputs, expected_revision=1)
    assert error.value.code == "VERSION_CONFLICT"
    assert inputs["result_store"].calls == []


def test_duplicate_initial_preparation_does_not_create_new_revision(inputs):
    first = prepare_collaboration_candidate(**inputs)
    second = prepare_collaboration_candidate(**inputs)
    assert first["candidate"] == second["candidate"]
    assert first["manifest"] == second["manifest"]
    assert first["research_package"] == second["research_package"]


def test_reusing_delivery_key_for_changed_draft_rejected(inputs):
    prepare_collaboration_candidate(**inputs)
    inputs["report_markdown"] = "# Changed draft\nDifferent report content."
    with pytest.raises(CollaborationError) as error:
        prepare_collaboration_candidate(**inputs)
    assert error.value.code == "DUPLICATE_CONFLICT"


def test_missing_backtest_does_not_register_task(inputs):
    inputs["backtest_id"] = "not-present"
    with pytest.raises(CollaborationError) as error:
        prepare_collaboration_candidate(**inputs)
    assert error.value.code == "ACCESS_DENIED"
    with pytest.raises(CollaborationError):
        inputs["evidence_store"].task(inputs["task"]["task_id"])
