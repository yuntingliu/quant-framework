from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from alphalab import ResultStore
from alphalab.dataio.recipes import render_builtin_recipe
from alphalab.dataio.runtime import OperationsStore
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
    assert "@factor" not in project["strategy_source"]
    assert {item["path"] for item in project["source_units"]} == {
        "strategy.py",
        "factors/momentum_20d.py",
    }
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

    default_response = TestClient(app).post(
        "/api/data-sync/connection-test",
        json={},
    )
    assert default_response.status_code == 200
    assert default_response.json()["template_id"] == "rq.a_share_research"


def test_data_recipe_workspace_uses_python_as_the_template_and_parameter_source(
    tmp_path,
    monkeypatch,
) -> None:
    manager = SimpleNamespace(operations=OperationsStore(tmp_path))
    monkeypatch.setattr(data_sync_service, "get_job_manager", lambda: manager)
    monkeypatch.setattr(
        data_sync_service,
        "_recipe_bounds",
        lambda: {"start": "2021-08-25", "end": "2026-08-25"},
    )
    client = TestClient(app)

    workspace = client.get("/api/data-sync/recipes/sdk-v1-default")
    assert workspace.status_code == 200, workspace.text
    payload = workspace.json()
    assert payload["docs_url"] == ("https://www.ricequant.com/doc/rqdata/python/index-rqdatac")
    assert payload["draft"]["inspection"]["matched_template_id"] == "rq.a_share_research"
    assert payload["draft"]["selected_template_id"] == "rq.a_share_research"
    assert "@data_recipe" in payload["draft"]["source"]
    assert "@universe" not in payload["draft"]["source"]

    applied = client.post(
        "/api/data-sync/recipes/sdk-v1-default/templates/rq.etf_daily",
        json={
            "expected_source_sha256": payload["draft"]["source_sha256"],
            "confirm_write": True,
        },
    )
    assert applied.status_code == 200, applied.text
    assert "template='rq.etf_daily'" in applied.json()["source"]
    assert "rq.all_instruments(" in applied.json()["source"]
    assert "rq.get_price(" in applied.json()["source"]
    assert "RQSyncRequest" not in applied.json()["source"]
    assert applied.json()["selected_template_id"] == "rq.etf_daily"

    projected = client.patch(
        "/api/data-sync/recipes/sdk-v1-default/parameters",
        json={
            "start": "2022-01-01",
            "end": "2026-08-25",
            "symbols": ["510300.XSHG"],
            "expected_source_sha256": applied.json()["source_sha256"],
            "confirm_write": True,
        },
    )
    assert projected.status_code == 200, projected.text
    source = projected.json()["source"]
    assert "start: str = '2022-01-01'" in source
    assert "symbols: tuple[str, ...] | None = ('510300.XSHG',)" in source
    assert projected.json()["selected_template_id"] == "rq.etf_daily"

    custom = client.post(
        "/api/data-sync/recipes/sdk-v1-default/templates",
        json={"name": "ETF custom", "description": "Saved Python", "confirm_save": True},
    )
    assert custom.status_code == 200, custom.text
    assert custom.json()["kind"] == "custom"
    custom_id = custom.json()["id"]
    assert custom.json()["draft"]["selected_template_id"] == custom_id

    refreshed = client.get("/api/data-sync/recipes/sdk-v1-default")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["draft"]["selected_template_id"] == custom_id

    reset_to_builtin = client.post(
        "/api/data-sync/recipes/sdk-v1-default/templates/rq.a_share_daily",
        json={
            "expected_source_sha256": refreshed.json()["draft"]["source_sha256"],
            "confirm_write": True,
        },
    )
    assert reset_to_builtin.status_code == 200, reset_to_builtin.text
    assert reset_to_builtin.json()["selected_template_id"] == "rq.a_share_daily"

    switched_back = client.post(
        f"/api/data-sync/recipes/sdk-v1-default/templates/{custom_id}",
        json={
            "expected_source_sha256": reset_to_builtin.json()["source_sha256"],
            "confirm_write": True,
        },
    )
    assert switched_back.status_code == 200, switched_back.text
    assert switched_back.json()["selected_template_id"] == custom_id
    assert switched_back.json()["source_sha256"] == custom.json()["source_sha256"]


def test_system_default_recipe_upgrades_only_an_untouched_previous_default(
    tmp_path,
    monkeypatch,
) -> None:
    operations = OperationsStore(tmp_path)
    old_source = render_builtin_recipe(
        "rq.a_share_daily",
        start="2021-08-25",
        end="2026-08-25",
    )
    operations.save_recipe_draft(
        "sdk-v1-default",
        old_source,
        selected_template_id="rq.a_share_daily",
    )
    operations.save_recipe_draft(
        "custom-project",
        old_source,
        selected_template_id="rq.a_share_daily",
    )
    manager = SimpleNamespace(operations=operations)
    monkeypatch.setattr(data_sync_service, "get_job_manager", lambda: manager)
    monkeypatch.setattr(strategy_service, "get_project", lambda project_id: {"id": project_id})
    monkeypatch.setattr(
        data_sync_service,
        "_recipe_bounds",
        lambda: {"start": "2021-08-25", "end": "2026-08-25"},
    )

    upgraded = data_sync_service.recipe_workspace("sdk-v1-default")["draft"]
    preserved = data_sync_service.recipe_workspace("custom-project")["draft"]

    assert upgraded["selected_template_id"] == "rq.a_share_research"
    assert upgraded["inspection"]["matched_template_id"] == "rq.a_share_research"
    assert preserved["selected_template_id"] == "rq.a_share_daily"
    assert preserved["inspection"]["matched_template_id"] == "rq.a_share_daily"


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
    assert "@factor" not in cloned.json()["strategy_source"]
    source_hash = cloned.json()["draft_source_sha256"]
    installed = client.post(
        "/api/strategy/projects/template-project/factor-templates/momentum_60d",
        json={"expected_source_sha256": source_hash, "confirm_write": True},
    )
    assert installed.status_code == 200, installed.text
    assert installed.json()["factor"]["id"] == "momentum_60d"
    project = installed.json()["project"]
    assert "def momentum_60d(context" in project["draft_source"]
    assert "@factor" not in project["strategy_source"]
    assert {item["path"] for item in project["source_units"]} >= {
        "strategy.py",
        "factors/momentum_20d.py",
        "factors/momentum_60d.py",
    }
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
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["factor"]["id"] == "momentum_60d_2"
    assert duplicate.json()["factor"]["function"] == "momentum_60d_2"
    assert duplicate.json()["factor"]["label"] == "60 日动量（副本 2）"
    project = duplicate.json()["project"]
    assert "def momentum_60d_2(context" in project["draft_source"]
    assert "factors/momentum_60d_2.py" in {
        item["path"] for item in project["source_units"]
    }

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


def test_strategy_and_factor_source_routes_keep_authoring_files_separate(
    tmp_path, monkeypatch
):
    database = tmp_path / "separate-source-routes.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    client = TestClient(app)
    cloned = client.post(
        "/api/strategy/projects/sdk-v1-default/clone",
        json={
            "target_id": "separate-source-routes",
            "name": "Separate Source Routes",
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert cloned.status_code == 201, cloned.text
    project = cloned.json()

    strategy_source = project["strategy_source"].replace(
        "top_n: int = 10", "top_n: int = 4"
    )
    updated = client.put(
        "/api/strategy/projects/separate-source-routes/draft",
        json={
            "source": strategy_source,
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": True,
        },
    )
    assert updated.status_code == 200, updated.text
    project = updated.json()
    assert "top_n: int = 4" in project["strategy_source"]
    assert "@factor" not in project["strategy_source"]
    assert "def momentum_20d(" in project["draft_source"]

    added = client.post(
        "/api/strategy/projects/separate-source-routes/factors",
        json={
            "source": '@factor(id="close_level")\ndef close_level(context):\n    return context.current("close")\n',
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": True,
        },
    )
    assert added.status_code == 201, added.text
    assert added.json()["factor"]["id"] == "close_level"
    project = added.json()["project"]
    assert "@factor" not in project["strategy_source"]
    assert "def close_level(context)" in project["draft_source"]
    assert "factors/close_level.py" in {
        item["path"] for item in project["source_units"]
    }


def test_visual_settings_batch_is_one_atomic_source_edit(tmp_path, monkeypatch):
    database = tmp_path / "visual-settings.db"
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    client = TestClient(app)
    cloned = client.post(
        "/api/strategy/projects/sdk-v1-default/clone",
        json={
            "target_id": "visual-settings-project",
            "name": "Visual Settings",
            "confirm_save": True,
            "confirm_python_execution": True,
        },
    )
    assert cloned.status_code == 201, cloned.text
    edits = [
        {
            "operation": "schedule",
            "entrypoint_id": "monthly_momentum",
            "frequency": "weekly",
            "selector": "last_trading_day",
            "at": "close",
        },
        {
            "operation": "factor_blend",
            "entrypoint_id": "monthly_momentum",
            "factor_weights": {"momentum_20d": 0.75},
            "normalization": "zscore",
        },
        {
            "operation": "parameter",
            "entrypoint_id": "monthly_momentum",
            "parameter": "top_n",
            "value": 3,
        },
    ]

    preview = client.post(
        "/api/strategy/projects/visual-settings-project/edits/preview",
        json={
            "edits": edits,
            "expected_source_sha256": cloned.json()["draft_source_sha256"],
        },
    )
    assert preview.status_code == 200, preview.text
    assert "Weekly.last_trading_day" in preview.json()["source"]
    assert "monthly_momentum" in preview.json()["source"]
    assert "@factor" not in preview.json()["source"]
    unchanged = client.get("/api/strategy/projects/visual-settings-project").json()
    assert unchanged["draft_source_sha256"] == cloned.json()["draft_source_sha256"]

    updated = client.post(
        "/api/strategy/projects/visual-settings-project/edits",
        json={
            "operation": "batch",
            "edits": edits,
            "expected_source_sha256": cloned.json()["draft_source_sha256"],
            "confirm_write": True,
        },
    )
    assert updated.status_code == 200, updated.text
    project = updated.json()["project"]
    signal = next(
        item for item in project["inspection"]["entrypoints"]
        if item["id"] == "monthly_momentum"
    )
    assert signal["metadata"]["schedule"]["frequency"] == "weekly"
    assert signal["metadata"]["factor_blend"]["weights"] == {"momentum_20d": 0.75}
    assert signal["metadata"]["factor_blend"]["normalization"] == "zscore"
    assert next(item for item in signal["parameters"] if item["name"] == "top_n")["default"] == 3
    assert project["current_revision"] == 1
    assert project["dirty"] is True

    rejected = client.post(
        "/api/strategy/projects/visual-settings-project/edits",
        json={
            "operation": "batch",
            "edits": [
                {
                    "operation": "schedule",
                    "entrypoint_id": "monthly_momentum",
                    "frequency": "daily",
                    "selector": "every",
                    "at": "open",
                },
                {
                    "operation": "parameter",
                    "entrypoint_id": "monthly_momentum",
                    "parameter": "missing_parameter",
                    "value": 1,
                },
            ],
            "expected_source_sha256": project["draft_source_sha256"],
            "confirm_write": True,
        },
    )
    assert rejected.status_code == 422
    unchanged = client.get("/api/strategy/projects/visual-settings-project").json()
    assert unchanged["draft_source_sha256"] == project["draft_source_sha256"]

    empty = client.post(
        "/api/strategy/projects/visual-settings-project/edits",
        json={"operation": "batch", "edits": [], "confirm_write": True},
    )
    assert empty.status_code == 422


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
    assert result["provenance"]["benchmark"]["source"] == "prepared_instrument_master"
    assert len(result["provenance"]["benchmark"]["symbols"]) == 300
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
