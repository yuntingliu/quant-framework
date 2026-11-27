from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import StrategyRepository
from alphalab.dataio.runtime import OperationsStore
from alphalab.validation.repository import ValidationRepository
from apps.api.routers import conexus
from apps.api.services import report_reference_service
from apps.api.services.workspace_output_service import (
    prepare_workspace_outputs,
    validate_workspace_delivery,
)


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / "alphalab.db"
    repo = StrategyRepository(path)
    repo.clone_project("sdk-v1-default", "research")
    package = repo.get_package("research")
    saved = repo.record_factor_evidence(
        "research", "momentum_20d", "main",
        {"project_id": "research", "revision": package["revision"], "source_sha256": package["source_sha256"], "status": "insufficient", "rows": []},
        {"start_date": "2024-01-01", "end_date": "2025-01-01", "parameters": {}},
        {"universe": "research_universe", "start_date": "2024-01-01", "end_date": "2025-01-01"},
        {"forward_return": "next_open_to_open"}, {"scope": "strategy_package"},
    )
    validation = ValidationRepository(path)
    validation.get_or_create("research")
    validation.close()
    with repo._conn as connection:
        connection.execute(
            "INSERT INTO backtests(id, config_yaml, strategy_project_id, strategy_revision, strategy_source_sha256, validation_revision) VALUES (?, '', 'research', ?, ?, 1)",
            ("run-1", package["revision"], package["source_sha256"]),
        )
    repo.close()
    operations = OperationsStore(tmp_path)
    operations.save_recipe_draft("research", "# recipe\n")
    store = report_reference_service.ReportReferenceStore(path, operations.path)
    return store, saved


def references(saved):
    return [
        {"kind": "strategy_package", "revision": 1},
        {"kind": "validation_package", "revision": 1},
        {"kind": "factor_evidence", "id": saved["id"]},
        {"kind": "recipe_revision", "revision": 1},
        {"kind": "backtest", "id": "run-1"},
    ]


def test_validate_reserve_confirm_and_idempotency(stores):
    store, saved = stores
    result = store.reserve("report-1", "req-1", "research", references(saved))
    assert result["status"] == "verified"
    assert len(result["reservations"]) == 5
    assert {item["status"] for item in result["references"]} == {"verified"}
    assert store.reserve("report-1", "req-1", "research", references(saved))["reservations"] == result["reservations"]
    store.confirm("report-1", "req-1")
    rows = store.list_reservations("report-1")
    assert len(rows) == 5
    assert {row["status"] for row in rows} == {"confirmed"}


def test_missing_and_unavailable_dependencies_are_explicit(stores):
    store, _ = stores
    result = store.validate("research", [
        {"kind": "strategy_package", "revision": 999},
        {"kind": "data_manifest"},
        {"kind": "experiment"},
    ])
    assert [item["status"] for item in result["references"]] == ["missing", "unavailable", "not_linked"]
    with pytest.raises(KeyError):
        store.validate("missing-project", [])


def test_validation_api_is_read_only(stores, monkeypatch):
    store, saved = stores
    monkeypatch.setattr(report_reference_service, "store", lambda: store)
    app = FastAPI()
    app.include_router(conexus.router)
    client = TestClient(app)
    response = client.post("/api/conexus/reports/validate-references", json={
        "project_id": "research", "references": references(saved),
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "verified"


def test_backtest_reports_mismatched_frozen_revisions(stores):
    store, _ = stores
    repo = StrategyRepository(store.alphalab_path)
    try:
        factor = repo.get_factor_source("research", "momentum_20d")["source"]
        repo.replace_factor_source("research", "momentum_20d", factor.replace("] = 20,", "] = 60,"))
        revision = repo.get_package("research")["revision"]
    finally:
        repo.close()
    result = store.validate("research", [
        {"kind": "strategy_package", "revision": revision},
        {"kind": "backtest", "id": "run-1"},
    ])
    assert result["status"] == "incomplete"
    package, backtest = result["references"]
    assert package["status"] == "verified"
    assert backtest["status"] == "mismatch"
    assert backtest["issues"][0]["actual_revision"] == 1


def test_missing_recipe_store_does_not_break_other_evidence(stores, tmp_path):
    existing, _ = stores
    absent = tmp_path / "uninitialized.db"
    store = report_reference_service.ReportReferenceStore(existing.alphalab_path, absent)
    assert store.validate("research", [])["status"] == "incomplete"
    strategy = {"kind": "strategy_package", "revision": 1}
    assert store.validate("research", [strategy])["status"] == "verified"
    result = store.validate("research", [strategy, {"kind": "recipe_revision", "revision": 1}])
    assert [item["status"] for item in result["references"]] == ["verified", "unavailable"]
    assert not absent.exists()


def test_manifest_must_match_provenance_of_a_linked_backtest(stores):
    import sqlite3

    store, _ = stores
    payload = {"version": 1, "datasets": ["rq.bars"], "files": [], "captured_at": "2026-11-27T00:00:00Z"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    manifest_id = hashlib.sha256(encoded).hexdigest()
    provenance = {
        "data_manifest_id": manifest_id,
        "data": {"kind": "data_snapshot", "manifest": {"id": manifest_id, **payload}},
    }

    def save_provenance():
        with sqlite3.connect(store.alphalab_path) as database:
            database.execute("UPDATE backtests SET provenance_json=? WHERE id='run-1'", (json.dumps(provenance),))

    save_provenance()
    backtest = {"kind": "backtest", "id": "run-1"}
    manifest = {"kind": "data_manifest", "id": manifest_id}
    result = store.validate("research", [manifest, backtest])
    assert result["status"] == "verified"
    assert result["references"][0]["details"]["backtest_id"] == "run-1"
    assert store.validate("research", [manifest])["status"] == "incomplete"
    provenance["data"]["manifest"]["datasets"] = ["rq.instruments"]
    save_provenance()
    assert store.validate("research", [manifest, backtest])["status"] == "incomplete"


def test_prepare_outputs_reserves_verified_references(stores, monkeypatch):
    store, saved = stores
    monkeypatch.setattr(report_reference_service, "store", lambda: store)
    outputs = {
        "summary": "Research summary",
        "decisionNotebook": {
            "classification": "review", "baseCase": "base", "riskCase": "risk",
            "nextAction": "next", "candidateExpressions": [],
        },
        "workspaceResult": {
            "version": 1, "reportId": "report-1", "projectId": "research", "requestId": "req-1",
            "kind": "document", "title": "Momentum", "sources": ["fixture-source"], "references": references(saved),
        },
        "workspaceCommands": {"version": 1, "requestId": "req-1", "commands": []},
    }
    result = prepare_workspace_outputs("req-1", outputs)
    assert result["status"] == "succeeded"
    assert result["reference_validation"]["status"] == "verified"
    assert result["operations"][-1]["node_id"] == "report-1"
    assert result["operations"][-1]["set"]["data"]["provenance"]["projectId"] == "research"
    assert result["operations"][-1]["set"]["data"]["sources"] == ["fixture-source"]
    assert len(store.list_reservations("report-1")) == 5


def output_payload(saved, request_id="req-1"):
    return {
        "summary": "Research summary",
        "decisionNotebook": {
            "classification": "review", "baseCase": "base", "riskCase": "risk",
            "nextAction": "next", "candidateExpressions": [],
        },
        "workspaceResult": {
            "version": 1, "reportId": "report-1", "projectId": "research", "requestId": request_id,
            "kind": "document", "title": "Momentum", "sources": [], "references": references(saved),
        },
        "workspaceCommands": {"version": 1, "requestId": request_id, "commands": []},
    }


def test_invalid_request_does_not_reserve(stores, monkeypatch):
    store, saved = stores
    monkeypatch.setattr(report_reference_service, "store", lambda: store)
    result = prepare_workspace_outputs("another-request", output_payload(saved))
    assert result["status"] == "failed"
    assert store.list_reservations("report-1") == []


def test_delivery_links_verified_metadata_without_writing(stores, monkeypatch):
    store, saved = stores
    monkeypatch.setattr(report_reference_service, "store", lambda: store)
    outputs = output_payload(saved)
    workspace = {
        "nodes": [
            {"id": "alphalab-decision-notebook-v1", "values": {"data": outputs["decisionNotebook"], "content": ""}, "updatedByRunId": "run-1"},
            {"id": "alphalab-workspace-result-v1", "values": {"data": outputs["workspaceResult"], "content": ""}, "updatedByRunId": "run-1"},
            {"id": "alphalab-workspace-commands-v1", "values": {"data": outputs["workspaceCommands"], "content": ""}, "updatedByRunId": "run-1"},
            {"id": "report-1", "type": "document", "description": "AlphaLab quantitative report", "values": {"content": "# Momentum", "data": {}}},
        ]
    }
    delivered = validate_workspace_delivery(workspace)
    report = delivered["nodes"][-1]
    provenance = report["values"]["data"]["provenance"]
    assert provenance["projectId"] == "research"
    assert len(provenance["references"]) == 5
    assert store.list_reservations("report-1") == []
