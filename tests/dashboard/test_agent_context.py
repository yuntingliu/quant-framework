from __future__ import annotations

from dashboard.backend.services import agent_context_service


def test_agent_context_reuses_one_bounded_runtime_catalog_snapshot(monkeypatch) -> None:
    calls = 0
    catalog = {
        "status": "ready",
        "ready": 1,
        "total": 1,
        "configured": 1,
        "datasets": [{"id": "rq.bars", "status": "ready"}],
    }

    class FakeCatalog:
        def summary(self) -> dict:
            nonlocal calls
            calls += 1
            return catalog

    monkeypatch.setattr(agent_context_service, "DataCatalog", FakeCatalog)
    monkeypatch.setattr(agent_context_service, "_catalog_cached_at", 0.0)
    monkeypatch.setattr(agent_context_service, "_catalog_cached_value", None)
    monkeypatch.setattr(
        agent_context_service.data_sync_service,
        "get_health",
        lambda *, runtime_summary: {"status": "ok", "runtime": runtime_summary, "rq": {"ready": True}},
    )
    monkeypatch.setattr(
        agent_context_service.data_sync_service,
        "jobs",
        lambda *, limit: [{
            "id": f"job-{limit}",
            "status": "completed",
            "request": {
                "kind": "recipe_source",
                "project_id": "project",
                "recipe_source": "secret source",
                "recipe_source_sha256": "abc",
                "symbols": ["A", "B"],
            },
        }],
    )
    monkeypatch.setattr(agent_context_service.strategy_service, "list_projects", lambda: [{"id": "project"}])
    monkeypatch.setattr(
        agent_context_service.strategy_service,
        "factor_template_catalog",
        lambda: {"templates": [{"id": "momentum", "source": "factor source"}]},
    )
    monkeypatch.setattr(agent_context_service, "list_backtests", lambda *, limit: [limit])

    first = agent_context_service.workspace_context(backtest_limit=3, sync_job_limit=4)
    second = agent_context_service.workspace_context(backtest_limit=2, sync_job_limit=1)

    assert calls == 1
    assert first["data_catalog"]["status"] == "ready"
    assert first["data_catalog"]["datasets"] == [{"id": "rq.bars", "status": "ready"}]
    assert "root" not in first["data_catalog"]
    assert "runtime" not in first["data_health"]
    assert first["backtests"] == [3]
    assert first["sync_jobs"][0]["id"] == "job-4"
    assert first["sync_jobs"][0]["request"]["symbol_count"] == 2
    assert "recipe_source" not in first["sync_jobs"][0]["request"]
    assert "source" not in first["factor_templates"][0]
    assert second["backtests"] == [2]
    assert second["sync_jobs"][0]["id"] == "job-1"
