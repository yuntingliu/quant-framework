from __future__ import annotations

from fastapi.testclient import TestClient

from alphalab import ResultStore
from alphalab.strategy.repository import StrategyRepository
from dashboard.backend.main import app
from dashboard.backend.services import (
    backtest_analytics_service,
    data_sync_service,
    result_service,
    strategy_service,
)


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
    entrypoint = client.get(
        "/api/strategy/projects/sdk-v1-default/entrypoints/monthly_momentum/source"
    )
    assert entrypoint.status_code == 200, entrypoint.text
    assert entrypoint.json()["source_sha256"] == project["draft_source_sha256"]
    assert entrypoint.json()["source"].startswith("@signal(")


def test_data_sync_templates_expose_rq_and_python_sdk_contracts() -> None:
    response = TestClient(app).get("/api/data-sync/templates")

    assert response.status_code == 200
    payload = response.json()
    assert {item["id"] for item in payload["templates"]} == {
        "rq.a_share_daily",
        "rq.etf_daily",
        "rq.exchange_fund_daily",
        "rq.a_share_research",
    }
    assert payload["python_sdk"]["import"] == "alphalab.data_sdk.v1"


def test_data_sync_connection_probe_uses_selected_template(monkeypatch) -> None:
    class FakeRQ:
        def get_latest_trading_date(self, **kwargs):
            assert kwargs == {"market": "cn"}
            return "2026-08-24"

    class FakeClient:
        def connect(self):
            return FakeRQ()

    monkeypatch.setattr(
        data_sync_service.RQDataClient,
        "from_env",
        lambda: FakeClient(),
    )
    monkeypatch.setattr(data_sync_service, "version", lambda _name: "3.6.3")

    response = TestClient(app).post(
        "/api/data-sync/connection-test",
        json={"template_id": "rq.etf_daily"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "connected",
        "connected": True,
        "template_id": "rq.etf_daily",
        "market": "cn",
        "instrument_types": ["ETF"],
        "latest_trading_date": "2026-08-24",
        "rqdatac_version": "3.6.3",
    }


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
    blended = client.post(
        "/api/strategy/projects/custom/edits",
        json={
            "operation": "factor_blend",
            "entrypoint_id": "monthly_momentum",
            "factor_weights": {"momentum_20d": 1.0},
            "normalization": "rank",
            "expected_source_sha256": edited.json()["project"]["draft_source_sha256"],
            "confirm_write": True,
        },
    )
    assert blended.status_code == 200, blended.text
    assert "context.combine_factors(" in blended.json()["project"]["draft_source"]
    saved = client.post(
        "/api/strategy/projects/custom/revisions",
        json={
            "expected_source_sha256": blended.json()["project"]["draft_source_sha256"],
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["revision"] == 2


def test_factor_template_catalog_and_install_use_the_strategy_draft(tmp_path, monkeypatch):
    database = tmp_path / "factor-templates.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    client = TestClient(app)

    catalog = client.get("/api/strategy/factor-templates")
    assert catalog.status_code == 200, catalog.text
    templates = catalog.json()["templates"]
    assert len(templates) == 16
    assert {item["id"] for item in templates} >= {"momentum_60d", "roe", "rsi_14"}
    assert all(item["source"].startswith("@factor(") for item in templates)

    cloned = client.post(
        "/api/strategy/projects/sdk-v1-default/clone",
        json={
            "target_id": "template-project",
            "name": "Template Project",
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert cloned.status_code == 201, cloned.text
    source_hash = cloned.json()["draft_source_sha256"]
    installed = client.post(
        "/api/strategy/projects/template-project/factor-templates/momentum_60d",
        json={"expected_source_sha256": source_hash, "confirm_write": True},
    )
    assert installed.status_code == 200, installed.text
    project = installed.json()["project"]
    assert "def momentum_60d(context" in project["draft_source"]
    assert {item["id"] for item in project["inspection"]["entrypoints"]} >= {
        "momentum_20d",
        "momentum_60d",
    }
    assert project["dirty"] is True

    blended = client.post(
        "/api/strategy/projects/template-project/edits",
        json={
            "operation": "factor_blend",
            "entrypoint_id": "monthly_momentum",
            "factor_weights": {"momentum_20d": 0.6, "momentum_60d": 0.4},
            "normalization": "rank",
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": True,
        },
    )
    assert blended.status_code == 200, blended.text
    project = blended.json()["project"]
    assert "'momentum_60d': 0.4" in project["draft_source"]

    duplicate = client.post(
        "/api/strategy/projects/template-project/factor-templates/momentum_60d",
        json={
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": True,
        },
    )
    assert duplicate.status_code == 422

    denied = client.post(
        "/api/strategy/projects/template-project/factor-templates/roe",
        json={
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": False,
        },
    )
    assert denied.status_code == 409

    saved = client.post(
        "/api/strategy/projects/template-project/revisions",
        json={
            "expected_source_sha256": project["draft_source_sha256"],
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
