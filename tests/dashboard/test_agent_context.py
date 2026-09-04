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
        lambda *, runtime_summary: {
            "status": "ok",
            "runtime": runtime_summary,
            "rq": {"ready": True},
        },
    )
    monkeypatch.setattr(
        agent_context_service.data_sync_service,
        "jobs",
        lambda *, limit: [
            {
                "id": f"job-{limit}",
                "status": "completed",
                "request": {
                    "kind": "recipe_source",
                    "project_id": "project",
                    "recipe_source": "secret source",
                    "recipe_source_sha256": "abc",
                    "symbols": ["A", "B"],
                },
            }
        ],
    )
    monkeypatch.setattr(
        agent_context_service.strategy_service,
        "list_projects",
        lambda: [
            {
                "id": "project",
                "name": "Project",
                "draft_source": "secret strategy source",
                "source_units": [
                    {"path": "strategy.py", "kind": "strategy"},
                    {"path": "factors/momentum.py", "kind": "factor"},
                ],
            }
        ],
    )
    monkeypatch.setattr(
        agent_context_service,
        "list_backtest_jobs",
        lambda *, limit: [
            {
                "id": f"backtest-job-{limit}",
                "status": "failed",
                "error_code": "INSUFFICIENT_MARKET_STATE",
                "error_summary": "C:\\private\\release\\worker.py failed",
                "request": {"project_id": "project", "source": "secret"},
            }
        ],
    )
    monkeypatch.setattr(
        agent_context_service,
        "list_backtests",
        lambda *, limit: [
            {
                "id": f"backtest-{limit}",
                "profile": "runtime",
                "code_version": "secret",
            }
        ],
    )

    first = agent_context_service.workspace_context(backtest_limit=3, sync_job_limit=4)
    second = agent_context_service.workspace_context(backtest_limit=2, sync_job_limit=1)

    assert calls == 1
    assert first["status"] == "ready"
    assert first["profile"] == "runtime"
    assert first["data_status"]["catalog"]["status"] == "ready"
    assert first["data_status"]["catalog"]["datasets"] == [{"id": "rq.bars", "status": "ready"}]
    assert "root" not in first["data_status"]["catalog"]
    assert first["projects"][0]["factor_count"] == 1
    assert "draft_source" not in first["projects"][0]
    assert first["recent_backtests"] == [{"id": "backtest-3", "profile": "runtime"}]
    assert first["tasks"]["sync"][0]["id"] == "job-4"
    assert first["tasks"]["sync"][0]["request"]["symbol_count"] == 2
    assert "recipe_source" not in first["tasks"]["sync"][0]["request"]
    assert first["tasks"]["backtest"][0]["error_code"] == "INSUFFICIENT_MARKET_STATE"
    assert "private" not in first["tasks"]["backtest"][0]["error_summary"]
    assert "source" not in first["tasks"]["backtest"][0]["request"]
    assert second["recent_backtests"] == [{"id": "backtest-2", "profile": "runtime"}]
    assert second["tasks"]["sync"][0]["id"] == "job-1"
