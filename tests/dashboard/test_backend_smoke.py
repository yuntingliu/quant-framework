from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from alphalab import PipelineRepository
from alphalab.factors.repository import FactorDefinitionRepository
from dashboard.backend.main import app
from dashboard.backend.routers import backtests, factor_research
from dashboard.backend.services import pipeline_service


STAGES = ["selection", "portfolio", "execution"]


def test_data_and_pipeline_read_contracts():
    client = TestClient(app)
    assert client.get("/").json()["status"] == "ok"

    providers = client.get("/api/data/providers")
    assert providers.status_code == 200, providers.text
    assert providers.json()["profiles"]["demo"]["status"] == "ready"

    symbols = client.get("/api/data/market/symbols", params={"profile": "demo"})
    assert symbols.status_code == 200, symbols.text
    assert symbols.json()["instruments"]
    assert {"symbol", "name"}.issubset(symbols.json()["instruments"][0])

    factor_library = client.get("/api/factor-research/library")
    assert factor_library.status_code == 200, factor_library.text
    factor_payload = factor_library.json()
    assert {"momentum_20d", "ep"}.issubset(
        {item["name"] for item in factor_payload["factors"]}
    )
    assert {"microsoft-qlib-alpha158", "microsoft-qlib-alpha360"} == {
        item["id"] for item in factor_payload["packs"]
    }
    assert all(item["status"] == "adapter_required" for item in factor_payload["packs"])
    momentum = next(item for item in factor_payload["factors"] if item["name"] == "momentum_20d")
    assert momentum["input_fields"] == ["close"]
    assert momentum["frequency"] == "daily"
    assert momentum["point_in_time"] is True
    sources = {item["id"]: item for item in factor_payload["data_sources"]}
    assert set(sources) == {"market_bars", "fundamentals"}
    assert sources["market_bars"]["endpoint"] == "/api/data/market/bars"
    assert sources["market_bars"]["profile"] == "demo"
    assert sources["market_bars"]["schema_source"] == "parquet_metadata"
    assert {item["name"] for item in sources["market_bars"]["fields"]} == {
        "date", "symbol", "open", "high", "low", "close", "volume", "amount"
    }
    assert not next(
        item for item in sources["market_bars"]["fields"] if item["name"] == "date"
    )["expression_compatible"]
    assert next(
        item for item in sources["market_bars"]["fields"] if item["name"] == "close"
    ) == {
        "name": "close",
        "label": "收盘价",
        "data_type": "number",
        "nullable": True,
        "expression_compatible": True,
    }
    fundamental_names = {item["name"] for item in sources["fundamentals"]["fields"]}
    assert {"symbol", "quarter", "available_date"}.issubset(fundamental_names)
    assert "roa" not in fundamental_names
    assert sources["fundamentals"]["point_in_time"] is True
    symbol = symbols.json()["symbols"][0]
    bars = client.get(
        "/api/data/market/bars",
        params={"profile": "demo", "symbol": symbol},
    )
    assert bars.status_code == 200, bars.text
    assert set(bars.json()["rows"][0]) == {
        item["name"] for item in sources["market_bars"]["fields"]
    }
    fundamental_rows = client.get(
        "/api/data/fundamentals",
        params={"profile": "demo", "symbols": symbol},
    )
    assert fundamental_rows.status_code == 200, fundamental_rows.text
    assert set(fundamental_rows.json()["rows"][0]) == fundamental_names

    projects = client.get("/api/pipeline/projects")
    assert projects.status_code == 200, projects.text
    assert any(item["id"] == "three-stage-default" for item in projects.json())

    project = client.get("/api/pipeline/projects/three-stage-default")
    assert project.status_code == 200, project.text
    payload = project.json()
    assert payload["built_in"] is True
    assert payload["editable"] is False
    assert list(payload["components"]) == STAGES
    assert [item["stage"] for item in payload["component_manifest"]] == STAGES
    assert payload["source_sha256"]
    assert "def run_strategy(context):" in payload["composed_source"]


def test_factor_evaluation_accepts_public_market_api_fields():
    client = TestClient(app)
    provider_payload = client.get("/api/data/providers").json()
    end = date.fromisoformat(provider_payload["profiles"]["demo"]["latest_date"])
    symbols = client.get(
        "/api/data/market/symbols", params={"profile": "demo"}
    ).json()["symbols"][:40]

    response = client.post(
        "/api/factor-research/evaluate",
        json={
            "profile": "demo",
            "name": "intraday_liquidity",
            "source": "expression",
            "expression": "zscore((close - open) / open) + zscore(log(amount))",
            "start_date": (end - timedelta(days=730)).isoformat(),
            "end_date": end.isoformat(),
            "frequency": "monthly",
            "quantiles": 5,
            "symbols": symbols,
            "min_history_days": 20,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["factor"]["expression"].startswith("zscore((close - open)")
    assert payload["periods"] > 0
    assert payload["snapshot"]["observations"] > 0


def test_custom_expression_factor_is_saved_back_to_the_reusable_library(tmp_path, monkeypatch):
    database = tmp_path / "factors.db"
    monkeypatch.setattr(
        factor_research,
        "factor_repository",
        lambda: FactorDefinitionRepository(database),
    )
    client = TestClient(app)

    saved = client.put(
        "/api/factor-research/factors/intraday_strength",
        json={
            "description": "日内价格强度",
            "expression": "zscore((close - open) / open)",
            "direction": "long",
            "winsorize": 0.02,
            "neutralize": ["market_cap"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["custom"] is True
    assert saved.json()["input_fields"] == ["close", "open"]

    library = client.get("/api/factor-research/library").json()["factors"]
    custom = next(item for item in library if item["name"] == "intraday_strength")
    assert custom["source"] == "expression"
    assert custom["expression"] == "zscore((close - open) / open)"
    assert custom["neutralize"] == ["market_cap"]

    protected = client.put(
        "/api/factor-research/factors/momentum_20d",
        json={"expression": "zscore(close)"},
    )
    assert protected.status_code == 422


def test_component_validation_requires_the_stage_entrypoint():
    client = TestClient(app)
    valid = client.post(
        "/api/pipeline/components/validate",
        json={
            "stage": "portfolio",
            "source": "def construct_portfolio(context):\n    return {'weights': {}}\n",
        },
    )
    assert valid.status_code == 200, valid.text
    assert valid.json()["valid"] is True
    assert valid.json()["sha256"]

    invalid = client.post(
        "/api/pipeline/components/validate",
        json={"stage": "portfolio", "source": "def wrong(context):\n    return {}\n"},
    )
    assert invalid.status_code == 422


def test_pipeline_crud_versions_and_reference_protection(tmp_path, monkeypatch):
    database = tmp_path / "pipeline.db"
    monkeypatch.setattr(pipeline_service, "repository", lambda: PipelineRepository(database))
    client = TestClient(app)

    cloned_component = client.post(
        "/api/pipeline/components/selection-factor-top/clone",
        json={"target_id": "my-selection", "name": "My Selection"},
    )
    assert cloned_component.status_code == 201, cloned_component.text
    assert cloned_component.json()["version"] == 1
    source = cloned_component.json()["source"] + "\n# immutable version 2\n"
    version = client.post(
        "/api/pipeline/components/my-selection/versions",
        json={"source": source, "parameters": {"label": "v2"}, "notes": "test"},
    )
    assert version.status_code == 201, version.text
    assert version.json()["version"] == 2

    cloned_project = client.post(
        "/api/pipeline/projects/three-stage-default/clone",
        json={"target_id": "my-project", "name": "My Project"},
    )
    assert cloned_project.status_code == 201, cloned_project.text
    project = cloned_project.json()
    project["components"]["selection"] = {"component_id": "my-selection", "version": 2}
    updated = client.put(
        "/api/pipeline/projects/my-project",
        json={
            "name": project["name"],
            "description": project["description"],
            "components": project["components"],
            "settings": project["settings"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert updated.json()["components"]["selection"]["version"] == 2

    protected = client.delete("/api/pipeline/components/my-selection")
    assert protected.status_code == 422
    assert client.delete("/api/pipeline/projects/my-project").status_code == 204
    assert client.delete("/api/pipeline/components/my-selection").status_code == 204


def test_preview_and_backtest_routes_use_project_ids(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(
        pipeline_service,
        "preview_project",
        lambda project_id, *, stage, profile, as_of_date: {
            "project_id": project_id,
            "requested_stage": stage,
            "profile": profile,
            "as_of_date": as_of_date,
            "source_sha256": "abc",
            "stages": {stage: {} for stage in STAGES},
            "targets": {},
        },
    )
    preview = client.post(
        "/api/pipeline/projects/three-stage-default/preview",
        json={"stage": "selection", "profile": "demo", "as_of_date": "2025-01-31"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["project_id"] == "three-stage-default"

    removed_stage_analysis = client.post(
        "/api/pipeline/projects/three-stage-default/analysis",
        json={"stage": "selection", "profile": "demo", "months": 12},
    )
    assert removed_stage_analysis.status_code == 404

    monkeypatch.setattr(
        backtests,
        "submit_backtest_job",
        lambda request: {
            "id": "job-1",
            "status": "queued",
            "request": request,
            "result": None,
            "result_id": None,
        },
    )
    result = client.post(
        "/api/backtests/jobs",
        json={
            "project_id": "three-stage-default",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "profile": "demo",
        },
    )
    assert result.status_code == 202, result.text
    assert result.json()["request"]["project_id"] == "three-stage-default"
    old_shape = client.post(
        "/api/backtests/jobs",
        json={
            "strategy_id": "balanced",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
    )
    assert old_shape.status_code == 422
    assert client.post("/api/backtests/run", json={}).status_code in {404, 405}


def test_removed_yaml_strategy_routes_are_not_exposed():
    client = TestClient(app)
    for path in ("/api/strategies", "/api/signals/generate", "/api/research/runs"):
        assert client.get(path).status_code == 404
