from __future__ import annotations

from fastapi.testclient import TestClient

from alphalab import PipelineRepository
from dashboard.backend.main import app
from dashboard.backend.routers import backtests
from dashboard.backend.services import pipeline_service


STAGES = ["universe", "selection", "timing", "portfolio", "risk", "execution"]


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

    projects = client.get("/api/pipeline/projects")
    assert projects.status_code == 200, projects.text
    assert any(item["id"] == "six-stage-default" for item in projects.json())

    project = client.get("/api/pipeline/projects/six-stage-default")
    assert project.status_code == 200, project.text
    payload = project.json()
    assert payload["built_in"] is True
    assert payload["editable"] is False
    assert list(payload["components"]) == STAGES
    assert [item["stage"] for item in payload["component_manifest"]] == STAGES
    assert payload["source_sha256"]
    assert "def run_strategy(context):" in payload["composed_source"]


def test_component_validation_requires_the_stage_entrypoint():
    client = TestClient(app)
    valid = client.post(
        "/api/pipeline/components/validate",
        json={"stage": "timing", "source": "def compute_exposure(context):\n    return {'exposure': 1.0}\n"},
    )
    assert valid.status_code == 200, valid.text
    assert valid.json()["valid"] is True
    assert valid.json()["sha256"]

    invalid = client.post(
        "/api/pipeline/components/validate",
        json={"stage": "timing", "source": "def wrong(context):\n    return {}\n"},
    )
    assert invalid.status_code == 422


def test_pipeline_crud_versions_and_reference_protection(tmp_path, monkeypatch):
    database = tmp_path / "pipeline.db"
    monkeypatch.setattr(pipeline_service, "repository", lambda: PipelineRepository(database))
    client = TestClient(app)

    cloned_component = client.post(
        "/api/pipeline/components/universe-all/clone",
        json={"target_id": "my-universe", "name": "My Universe"},
    )
    assert cloned_component.status_code == 201, cloned_component.text
    assert cloned_component.json()["version"] == 1
    source = cloned_component.json()["source"] + "\n# immutable version 2\n"
    version = client.post(
        "/api/pipeline/components/my-universe/versions",
        json={"source": source, "parameters": {"label": "v2"}, "notes": "test"},
    )
    assert version.status_code == 201, version.text
    assert version.json()["version"] == 2

    cloned_project = client.post(
        "/api/pipeline/projects/six-stage-default/clone",
        json={"target_id": "my-project", "name": "My Project"},
    )
    assert cloned_project.status_code == 201, cloned_project.text
    project = cloned_project.json()
    project["components"]["universe"] = {"component_id": "my-universe", "version": 2}
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
    assert updated.json()["components"]["universe"]["version"] == 2

    protected = client.delete("/api/pipeline/components/my-universe")
    assert protected.status_code == 422
    assert client.delete("/api/pipeline/projects/my-project").status_code == 204
    assert client.delete("/api/pipeline/components/my-universe").status_code == 204


def test_preview_and_backtest_routes_use_project_ids(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(
        pipeline_service,
        "preview_project",
        lambda project_id, *, profile, as_of_date: {
            "project_id": project_id,
            "profile": profile,
            "as_of_date": as_of_date,
            "source_sha256": "abc",
            "stages": {stage: {} for stage in STAGES},
            "targets": {},
        },
    )
    preview = client.post(
        "/api/pipeline/projects/six-stage-default/preview",
        json={"profile": "demo", "as_of_date": "2025-01-31"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["project_id"] == "six-stage-default"

    monkeypatch.setattr(
        backtests,
        "run_project_backtest",
        lambda project_id, start_date, end_date, profile: {
            "id": "run-1",
            "project_id": project_id,
            "source_sha256": "abc",
            "profile": profile,
            "dates": [start_date, end_date],
        },
    )
    result = client.post(
        "/api/backtests/run",
        json={
            "project_id": "six-stage-default",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "profile": "demo",
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["project_id"] == "six-stage-default"
    old_shape = client.post(
        "/api/backtests/run",
        json={
            "strategy_id": "balanced",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
    )
    assert old_shape.status_code == 422


def test_removed_yaml_strategy_routes_are_not_exposed():
    client = TestClient(app)
    for path in ("/api/strategies", "/api/signals/generate", "/api/research/runs"):
        assert client.get(path).status_code == 404
