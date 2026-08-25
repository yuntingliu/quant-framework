from __future__ import annotations

from fastapi.testclient import TestClient

from alphalab import ResultStore
from alphalab.strategy.repository import StrategyRepository
from dashboard.backend.main import app
from dashboard.backend.services import backtest_analytics_service, result_service, strategy_service


def test_data_and_strategy_sdk_read_contracts(tmp_path, monkeypatch):
    database = tmp_path / "strategy.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    client = TestClient(app)
    assert client.get("/").json()["status"] == "ok"
    providers = client.get("/api/data/providers")
    assert providers.status_code == 200
    assert providers.json()["profiles"]["demo"]["status"] == "ready"
    fields = client.get("/api/strategy/fields", params={"profile": "demo"})
    assert fields.status_code == 200
    assert {"open", "close", "volume", "amount"} <= {
        item["name"] for item in fields.json()["datasets"]["market_bars"]
    }
    projects = client.get("/api/strategy/projects")
    assert projects.status_code == 200
    assert projects.json()[0]["id"] == "sdk-v1-default"
    project = client.get("/api/strategy/projects/sdk-v1-default").json()
    assert project["current_package"]["sdk_version"] == 1
    assert project["inspection"]["source_sha256"] == project["draft_source_sha256"]
    assert "@factor" in project["draft_source"]


def test_strategy_clone_cst_edit_and_revision_confirmation(tmp_path, monkeypatch):
    database = tmp_path / "strategy.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    client = TestClient(app)
    denied = client.post(
        "/api/strategy/projects/sdk-v1-default/clone",
        json={"target_id": "custom", "confirm_save": True, "confirm_python_execution": False},
    )
    assert denied.status_code == 409
    cloned = client.post(
        "/api/strategy/projects/sdk-v1-default/clone",
        json={
            "target_id": "custom",
            "name": "Custom",
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert cloned.status_code == 201, cloned.text
    original_hash = cloned.json()["draft_source_sha256"]
    edited = client.post(
        "/api/strategy/projects/custom/edits",
        json={
            "operation": "parameter",
            "entrypoint_id": "momentum_20d",
            "parameter": "window",
            "value": 30,
            "expected_source_sha256": original_hash,
            "confirm_write": True,
        },
    )
    assert edited.status_code == 200, edited.text
    edited_source = edited.json()["project"]["draft_source"]
    assert 'Parameter(label="窗口", minimum=2, maximum=500, step=1)' in edited_source
    assert "] = 30" in edited_source
    assert edited.json()["project"]["draft_source_sha256"] != original_hash
    saved = client.post(
        "/api/strategy/projects/custom/revisions",
        json={
            "expected_source_sha256": edited.json()["project"]["draft_source_sha256"],
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["revision"] == 2


def test_legacy_authoring_routes_are_not_mounted():
    client = TestClient(app)
    for path in (
        "/api/pipeline/projects",
        "/api/factor-research/library",
        "/api/python-lab/capabilities",
    ):
        assert client.get(path).status_code == 404


def test_complete_sdk_backtest_persists_revision_hash_and_manifest(tmp_path, monkeypatch):
    database = tmp_path / "runs.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(strategy_service, "ResultStore", lambda: ResultStore(database))
    result = strategy_service.run_project_backtest(
        "sdk-v1-default", "2025-01-01", "2025-03-31", "demo", 1
    )
    assert result["revision"] == 1
    assert result["source_sha256"]
    store = ResultStore(database)
    try:
        record = store.get_backtest_record(result["id"])
    finally:
        store.close()
    assert record is not None
    assert record["strategy_project_id"] == "sdk-v1-default"
    assert record["strategy_revision"] == 1
    assert record["strategy_source_sha256"] == result["source_sha256"]
    assert record["strategy_manifest_json"]
    monkeypatch.setattr(result_service, "ResultStore", lambda: ResultStore(database))
    detail = result_service.get_backtest(result["id"])
    assert detail is not None
    analysis = backtest_analytics_service.analyze_record(detail)
    assert analysis["strategy_snapshot"]["strategy_type"] == "sdk_v1"
    assert analysis["strategy_snapshot"]["revision"] == 1
    assert analysis["strategy_snapshot"]["source_sha256"] == result["source_sha256"]
