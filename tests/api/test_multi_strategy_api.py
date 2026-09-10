"""Multi-strategy API selection, durable batch inputs, and real engine results."""

from __future__ import annotations

import time
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import ResultStore, StrategyRepository, create_default_engine
from alphalab.validation.repository import ValidationRepository
from apps.api.routers import backtests, strategy
from apps.api.services import (
    backtest_analytics_service,
    backtest_job_service,
    result_service,
    strategy_service,
    validation_service,
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    database = tmp_path / "project.db"
    repo = StrategyRepository(database)
    repo.clone_project("sdk-v1-default", "research")
    extra = repo.create_strategy("research", "focused", name="集中策略")
    repo.update_strategy_source("research", extra["strategy_source"].replace("top_n: int = 10", "top_n: int = 2"))
    repo.close()
    validation = ValidationRepository(database)
    validation.get_or_create("research")
    validation.close()
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(database))
    monkeypatch.setattr(validation_service, "repository", lambda: ValidationRepository(database))
    monkeypatch.setattr(strategy_service, "ResultStore", lambda: ResultStore(database))
    monkeypatch.setattr(result_service, "ResultStore", lambda: ResultStore(database))
    app = FastAPI()
    app.include_router(strategy.router)
    app.include_router(backtests.router)
    return database, TestClient(app)


def test_api_selected_strategy_save_preview_and_invalid_selection(project):
    _, client = project
    base = "/api/strategy/projects/research"
    main = client.get(base).json()
    selected = client.get(base + "?strategy_id=focused").json()
    assert "top_n: int = 2" in selected["strategy_source"]
    assert "top_n: int = 10" in main["strategy_source"]
    preview = client.post(base + "/edits/preview?strategy_id=focused", json={
        "edits": [{"operation": "parameter", "entrypoint_id": "monthly_momentum", "parameter": "top_n", "value": 4}],
        "expected_source_sha256": selected["draft_source_sha256"],
    })
    assert preview.status_code == 200, preview.text
    assert "top_n: int = 4" in preview.json()["source"]
    response = client.put(base + "/draft?strategy_id=focused", json={
        "source": preview.json()["source"], "expected_source_sha256": selected["draft_source_sha256"],
        "confirm_write": True, "confirm_python_execution": True,
    })
    assert response.status_code == 200, response.text
    assert response.json()["strategy_id"] == "focused"
    assert client.get(base).json()["draft_source_sha256"] == main["draft_source_sha256"]
    assert client.get(base + "?strategy_id=missing").status_code == 404
    assert client.get(base + f"/revisions/{response.json()['current_revision']}").status_code == 404
    created = client.post(base + "/strategies", json={"strategy_id": "copy", "name": "副本", "copy_from": "focused", "confirm_write": True})
    assert created.status_code == 201, created.text
    assert created.json()["strategy_source"] == response.json()["strategy_source"]
    assert client.post(base + "/strategies", json={"strategy_id": "../escape", "name": "invalid", "confirm_write": True}).status_code == 422


def test_batch_pins_sources_and_settings_and_continues_after_failure(project, monkeypatch):
    database, client = project
    release = Event()
    started = Event()
    calls = []

    def run(project_id, start, end, profile, revision, *, strategy_id, settings, strategy_name, batch_id, backtest_id):
        started.set()
        assert release.wait(10)
        calls.append({"strategy_id": strategy_id, "revision": revision, "settings": settings,
                      "strategy_name": strategy_name, "batch_id": batch_id})
        if strategy_id == "main":
            raise ValueError("controlled first strategy failure")
        return {"id": backtest_id, "project_id": project_id, "metrics": {"sharpe": 1.0}}

    manager = backtest_job_service.BacktestJobManager(database, runner=run)
    monkeypatch.setattr(backtest_job_service, "manager", lambda: manager)
    payload = {"project_id": "research", "strategy_ids": ["main", "focused"], "start_date": "2025-01-01", "end_date": "2025-03-31", "confirm_python_execution": True}
    try:
        assert client.post("/api/backtests/batches", json={**payload, "strategy_ids": ["main", "missing"]}).status_code == 404
        assert manager.list() == []
        response = client.post("/api/backtests/batches", json=payload)
        assert response.status_code == 202, response.text
        jobs = response.json()["jobs"]
        assert started.wait(3)
        assert len(jobs) == 2
        before = [job["request"] for job in jobs]
        changed = strategy_service.get_project("research", "focused")
        strategy_service.update_strategy_source("research", changed["strategy_source"].replace("top_n: int = 2", "top_n: int = 5"), strategy_id="focused")
        strategy_service.update_metadata("research", {"name": "updated", "description": "", "profile": "runtime", "settings": {"lookback_days": 99}})
        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            results = [manager.get(job["id"]) for job in jobs]
            if all(job["status"] not in {"queued", "running"} for job in results):
                break
            time.sleep(0.02)
        assert [job["status"] for job in results] == ["failed", "succeeded"]
        assert [call["revision"] for call in calls] == [request["revision"] for request in before]
        assert all(call["settings"] == before[0]["settings"] for call in calls)
        assert calls[1]["revision"] != strategy_service.get_project("research", "focused")["current_revision"]
        assert len({call["batch_id"] for call in calls}) == 1
    finally:
        release.set()
        manager.shutdown()


def test_real_engine_persists_distinct_strategy_identity_and_comparison(project, monkeypatch):
    _, client = project
    monkeypatch.setattr(strategy_service, "_engine", lambda _profile: create_default_engine())
    results = [strategy_service.run_project_backtest("research", "2025-01-01", "2025-03-31", "demo", strategy_id=selected)
               for selected in ("main", "focused")]
    assert len({row["source_sha256"] for row in results}) == 2
    assert {row["project_strategy_id"] for row in results} == {"main", "focused"}
    records = client.get("/api/backtests").json()
    assert {row["project_id"] for row in records} == {"research"}
    assert {row["project_strategy_id"] for row in records} == {"main", "focused"}
    ids = [row["id"] for row in results]
    comparison = backtest_analytics_service.compare_backtests(ids)
    assert comparison["dates"]
    assert comparison["warnings"] == []
    assert {row["strategy_name"] for row in comparison["metrics"]} == {"主策略", "集中策略"}
    original = result_service.get_backtest(ids[1])
    strategy_service.rename_project_strategy("research", "focused", "新名称")
    assert result_service.get_backtest(ids[1])["strategy_name"] == original["strategy_name"]
