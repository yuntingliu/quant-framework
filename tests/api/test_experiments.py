from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import StrategyRepository
from alphalab.strategy.source import update_parameter_default
from alphalab.validation.repository import ValidationRepository
from apps.api.routers import experiments


@pytest.fixture
def setup_experiment(tmp_path):
    path = tmp_path / "experiments.db"
    repository = StrategyRepository(path)
    repository.clone_project("sdk-v1-default", "experiment-project")
    validation = ValidationRepository(path)
    validation.get_or_create("experiment-project")
    validation.close()
    spec = {
        "project_id": "experiment-project", "base_revision": 1, "validation_revision": 1,
        "candidates": [{"momentum_20d": {"window": 10}}, {"momentum_20d": {"window": 30}}],
        "validation": {"start": "2024-01-01", "end": "2024-12-31"},
        "test": {"start": "2025-01-01", "end": "2025-12-31"}, "max_trials": 3,
    }
    yield repository, spec
    repository.close()


def test_experiment_freezes_settings_sources_and_survives_reopen(setup_experiment):
    repository, spec = setup_experiment
    before = repository.get_project(spec["project_id"])
    result = repository.create_experiment(spec)
    assert repository.get_project(spec["project_id"]) == before
    assert len(result["candidates"]) == len(result["trials"]) == 2
    assert {trial["stage"] for trial in result["trials"]} == {"validation"}
    assert {trial["status"] for trial in result["trials"]} == {"planned"}
    assert result["protocol"]["data_snapshot_status"] == "missing"
    assert result["protocol"]["execution_status"] == "not_scheduled"
    assert repository._conn.execute("SELECT COUNT(*) FROM backtest_jobs").fetchone()[0] == 0
    changed, _ = update_parameter_default(
        before["draft_source"], entrypoint_id="momentum_20d", parameter="window", value=50
    )
    repository.update_draft(spec["project_id"], changed)
    repository._conn.execute(
        "UPDATE strategy_projects SET settings_json = '{}' WHERE id = ?", (spec["project_id"],)
    )
    repository._conn.commit()
    reopened = StrategyRepository(repository.path)
    try:
        assert reopened.get_experiment(result["id"]) == result
        assert reopened.list_experiments(spec["project_id"]) == [result]
        for candidate in result["candidates"]:
            package = reopened.get_package(spec["project_id"], candidate["revision"])
            assert package["parameters"]["momentum_20d"] == candidate["parameters"]["momentum_20d"]
            assert package["parent_revision"] == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("failure", ["invalid_second_candidate", "trial_insert"])
def test_experiment_creation_rolls_back_all_records(setup_experiment, failure):
    repository, spec = setup_experiment
    before = repository.list_packages(spec["project_id"])
    if failure == "invalid_second_candidate":
        spec["candidates"][1]["momentum_20d"]["window"] = 1
    else:
        repository._conn.execute(
            """CREATE TRIGGER fail_trial BEFORE INSERT ON experiment_trials
               WHEN NEW.candidate_id = '2' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"""
        )
    with pytest.raises(Exception, match="below its minimum|injected failure"):
        repository.create_experiment(spec)
    assert repository.list_packages(spec["project_id"]) == before
    for table in ("research_experiments", "experiment_candidates", "experiment_trials"):
        assert repository._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("change", [
    {"test": {"start": "2024-12-31", "end": "2025-12-31"}},
    {"training": {"start": "2023-01-01", "end": "2024-01-01"}},
    {"validation": {"start": "2024-12-31", "end": "2024-01-01"}},
    {"max_trials": 2}, {"max_concurrency": 2}, {"selection_metric": "unknown"},
])
def test_experiment_rejects_invalid_protocol_before_writing(setup_experiment, change):
    repository, spec = setup_experiment
    with pytest.raises(ValueError):
        repository.create_experiment({**spec, **change})
    assert repository.list_experiments(spec["project_id"]) == []
    assert len(repository.list_packages(spec["project_id"])) == 1


def test_experiment_queries_preserve_failed_and_cancelled_trials(setup_experiment):
    repository, spec = setup_experiment
    result = repository.create_experiment(spec)
    for trial, status in zip(result["trials"], ("failed", "cancelled"), strict=True):
        repository._conn.execute(
            "UPDATE experiment_trials SET status = ?, error = ? WHERE id = ?",
            (status, "test fixture outcome", trial["id"]),
        )
    repository._conn.commit()
    recorded = repository.get_experiment(result["id"])
    assert [trial["status"] for trial in recorded["trials"]] == ["failed", "cancelled"]
    assert all(trial["error"] == "test fixture outcome" for trial in recorded["trials"])


def test_experiments_reuse_identical_packages_without_sharing_trial_records(setup_experiment):
    repository, spec = setup_experiment
    first = repository.create_experiment(spec)
    second = repository.create_experiment(spec)
    assert first["id"] != second["id"]
    assert [item["revision"] for item in first["candidates"]] == [item["revision"] for item in second["candidates"]]
    assert {item["id"] for item in first["trials"]}.isdisjoint(item["id"] for item in second["trials"])
    assert len(repository.list_packages(spec["project_id"])) == 3


def test_experiment_api_requires_confirmation_and_exposes_records(setup_experiment, monkeypatch):
    repository, spec = setup_experiment
    monkeypatch.setattr(experiments, "StrategyRepository", lambda: StrategyRepository(repository.path))
    app = FastAPI()
    app.include_router(experiments.router)
    with TestClient(app) as client:
        assert client.post("/api/experiments", json=spec).status_code == 409
        assert repository.list_experiments(spec["project_id"]) == []
        payload = {**spec, "confirm_python_execution": True, "data_manifest_id": "future-snapshot"}
        response = client.post("/api/experiments", json=payload)
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["protocol"]["data_snapshot_status"] == "unverified"
        assert client.get(f"/api/experiments/{result['id']}").json() == result
        assert client.get("/api/experiments", params={"project_id": spec["project_id"]}).json() == [result]
        assert client.get("/api/experiments/missing").status_code == 404
        assert client.post("/api/experiments", json={**payload, "validation_revision": 999}).status_code == 404
        invalid = deepcopy(payload)
        invalid["candidates"][1]["momentum_20d"]["window"] = 1
        assert client.post("/api/experiments", json=invalid).status_code == 422
        duplicate = {**payload, "candidates": [spec["candidates"][0]] * 2}
        assert client.post("/api/experiments", json=duplicate).status_code == 422
