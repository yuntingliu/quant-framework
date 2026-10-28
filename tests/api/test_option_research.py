"""Option feature/projection APIs use the canonical source and project contracts."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import StrategyRepository
from alphalab.dataio.runtime import OperationsStore
from apps.api.routers import options
from apps.api.services import data_sync_service, options_research_service, strategy_service

app = FastAPI()
app.include_router(options.router)
client = TestClient(app)


def test_controlled_suite_routes_preserve_the_predeclared_family(monkeypatch):
    from apps.api.services import options_evidence_service, options_rotation_service
    captured = []
    monkeypatch.setattr(options_evidence_service, "run_suite", lambda body: captured.append(body) or {"id":"a"*20,"tests":24})
    response = client.post("/api/options/research/suites/run", json={
        "underlyings":["510050.XSHG"],"fields":["option_smirk"],"start":"2023-06-05","end":"2026-10-23","holdout_start":"2025-01-02"})
    assert response.status_code == 200 and response.json()["tests"] == 24
    assert "horizon" not in captured[0]
    assert client.get("/api/options/research/suites/invalid").status_code == 422
    denied = client.post("/api/options/research/suites/"+"a"*20+"/rotation",json={"horizon":5})
    assert denied.status_code == 422
    monkeypatch.setattr(options_rotation_service, "rotation_pair", lambda suite_id, horizon: {"suite_id":suite_id,"horizon":horizon})
    response = client.get("/api/options/research/suites/"+"a"*20+"/rotation?horizon=5")
    assert response.status_code == 200 and response.json()["horizon"] == 5


def test_research_catalog_defines_distinct_bilingual_predictors():
    response = client.get("/api/options/research/catalog")
    assert response.status_code == 200
    data = response.json()
    fields = {s["field"]: s for s in data["features"]}
    assert len(fields) == 19
    assert fields["option_smirk"]["formula"] != fields["option_rr25_30"]["formula"]
    assert fields["option_vrp_hf_30"]["unit"] == "annual_variance"
    assert all(s["zh"] and s["en"] for s in fields.values())


def test_feature_plan_uses_visible_local_recipe_without_execution_confirmation():
    result = client.post(
        "/api/options/features/plan",
        json={"underlyings": ["510050.SH"], "start": "2025-01-06", "end": "2025-01-10"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["planned"][0]["dataset"] == "research.option_features"
    assert "iter_feature_batches" in result.json()["source"]
    assert "510050.XSHG" in result.json()["source"]


@pytest.mark.parametrize(
    "body",
    [
        {"research_id": "../../secret"},
        {"weights": {"future_return": 1}},
        {"weights": {"option_smirk": 0}},
        {"top_n": 20},
    ],
)
def test_project_planning_rejects_unregistered_predictors_and_bad_identifiers(body):
    result = client.post(
        "/api/options/research/projects/plan",
        json={
            "project_id": "options-test",
            "name": "Options test",
            "end": "2025-12-31",
            "underlyings": ["510050.XSHG"],
            "weights": {"option_smirk": -1},
            **body,
        },
    )
    assert result.status_code == 422


def test_option_project_creates_real_sdk_units_and_comprehensive_recipe(tmp_path, monkeypatch):
    database = tmp_path / "app.db"
    repo = StrategyRepository(database)
    repo.close()
    operations = OperationsStore(tmp_path / "runtime")
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(strategy_service, "operations_store", lambda: operations)
    monkeypatch.setattr(
        data_sync_service, "get_job_manager", lambda: SimpleNamespace(operations=operations)
    )
    monkeypatch.setattr(
        data_sync_service, "_recipe_bounds", lambda: {"start": "2020-01-01", "end": "2026-01-01"}
    )
    spec = {
        "project_id": "options-test",
        "name": "Option research",
        "start": "2023-01-03",
        "end": "2025-12-31",
        "underlyings": ["510050.XSHG", "510300.XSHG"],
        "weights": {"option_smirk": -1, "option_rr25_30": 1},
        "top_n": 2,
        "frequency": "weekly",
    }
    result = options_research_service.create_research_project(spec)
    project = result["project"]
    assert project["id"] == "options-test" and project["editable"]
    assert 'context.daily_factor("option_smirk")' in project["draft_source"]
    assert "Weekly.last_trading_day" in project["strategy_source"]
    assert "510050.SH" in project["strategy_source"]
    draft = operations.get_recipe_draft("options-test")
    assert draft["selected_template_id"] == "rq.etf_options"
    assert 'frequency="5m"' in draft["source"]
    assert "INCLUDE_RESEARCH = True" in draft["source"]
    workspace = data_sync_service.recipe_workspace("options-test")
    assert workspace["draft"]["source"] == draft["source"]
    assert workspace["draft"]["selected_template_id"] == "rq.etf_options"
    assert workspace["migration"]["applied"] is False


@pytest.mark.parametrize("template", ["rq.etf_options", "research.option_features"])
def test_new_recipe_connection_probe_uses_the_visible_catalog(template, monkeypatch):
    fake = SimpleNamespace(get_latest_trading_date=lambda **kwargs: "2026-10-23")
    monkeypatch.setattr(
        data_sync_service.RQDataClient, "from_env", lambda: SimpleNamespace(connect=lambda: fake)
    )
    response = data_sync_service.test_connection(template)
    assert response["connected"] and response["template_id"] == template
    assert response["latest_trading_date"] == "2026-10-23"
