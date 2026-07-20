from __future__ import annotations

from fastapi.testclient import TestClient

from alphalab import ResultStore
from dashboard.backend.main import app
from dashboard.backend.services import framework_service, research_service


def test_backend_smoke_endpoints():
    client = TestClient(app)
    assert client.get("/").json()["status"] == "ok"
    providers = client.get("/api/data/providers")
    assert providers.status_code == 200
    assert providers.json()["symbol_count"] == 300
    assert all(item["status"] == "ready" for item in providers.json()["datasets"].values())
    manifest = client.get("/api/data/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["realtime"] == "not_configured"
    strategies = client.get("/api/strategies")
    assert strategies.status_code == 200
    assert {item["id"] for item in strategies.json()} >= {"momentum", "balanced"}
    assert client.get("/api/system/logs").status_code == 200
    health = client.get("/api/data-sync/health")
    assert health.status_code == 200
    assert health.json()["tools"]["status"] == "ready"
    assert health.json()["planner"]["status"] == "not_configured"
    catalog = client.get("/api/data-sync/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["datasets"]


def test_real_data_endpoints():
    client = TestClient(app)
    symbols = client.get("/api/data/market/symbols").json()["symbols"]
    assert len(symbols) == 300
    bars = client.get(f"/api/data/market/bars?symbol={symbols[0]}&start=2026-01-01")
    assert bars.status_code == 200
    assert bars.json()["rows"]
    factors = client.get("/api/data/factors/returns")
    assert factors.status_code == 200
    assert factors.json()["names"] == ["MKT", "SMB", "HML", "MOM", "RMW", "rf"]
    assert client.get("/api/data/market/bars?symbol=NOT-A-SYMBOL").status_code == 404
    assert client.get("/api/data/market/symbols?profile=unknown").status_code == 422


def test_market_analytics_endpoints():
    client = TestClient(app)
    endpoints = (
        "/api/market/kpi",
        "/api/market/cumulative-returns",
        "/api/market/factor-stats",
        "/api/market/drawdowns",
        "/api/market/annual-returns",
        "/api/market/volatility",
        "/api/market/correlation",
    )
    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        assert response.json()["profile"] == "demo"

    cumulative = client.get("/api/market/cumulative-returns").json()
    assert cumulative["dates"]
    assert set(cumulative["series"]) == {"MKT", "SMB", "HML"}
    assert client.get("/api/market/kpi?profile=runtime").status_code == 503
    assert client.get("/api/market/kpi?start=2026-01-01&end=2025-01-01").status_code == 422
    assert client.get("/api/market/correlation?factors=UNKNOWN").status_code == 422


def test_data_sync_plan_and_submit_contract(monkeypatch):
    client = TestClient(app)
    plan = client.post(
        "/api/data-sync/plan",
        json={
            "source": "rq",
            "datasets": ["bars", "fundamentals"],
            "symbols": ["000001.SZ"],
            "start": "2024-01-01",
            "end": "2025-01-01",
        },
    )
    assert plan.status_code == 200
    assert plan.json()["writes_are_local"] is True
    assert plan.json()["symbol_count"] == 1

    monkeypatch.setattr(
        "dashboard.backend.services.data_sync_service.submit",
        lambda request: {
            "id": "job-1",
            "source": "rq",
            "status": "queued",
            "request": request.model_dump(mode="json"),
        },
    )
    submitted = client.post(
        "/api/data-sync/jobs",
        json={"source": "rq", "datasets": ["bars"], "symbols": ["000001.SZ"]},
    )
    assert submitted.status_code == 202
    assert submitted.json()["status"] == "queued"


def test_backtest_signal_and_paper_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(framework_service, "ResultStore", lambda: ResultStore(tmp_path / "alphalab.db"))

    client = TestClient(app)
    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_id": "momentum",
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy_id"] == "momentum"
    assert payload["returns"]
    assert payload["weights_count"] > 0
    assert payload["metrics"]["n_periods"] > 0

    detail = client.get(f"/api/backtests/{payload['id']}")
    assert detail.status_code == 200
    assert detail.json()["returns"]
    assert detail.json()["weights"]

    analysis = client.get(f"/api/backtests/{payload['id']}/analysis")
    assert analysis.status_code == 200
    assert analysis.json()["equity_curve"]
    assert analysis.json()["holdings"]
    robustness = client.get(f"/api/backtests/{payload['id']}/robustness")
    assert robustness.status_code == 200
    assert robustness.json()["status"] in {
        "research_candidate",
        "watch",
        "weak",
        "invalid",
    }

    second = client.post(
        "/api/backtests/run",
        json={
            "strategy_id": "value",
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        },
    )
    assert second.status_code == 200
    comparison = client.post(
        "/api/backtests/compare",
        json={"ids": [payload["id"], second.json()["id"]]},
    )
    assert comparison.status_code == 200
    assert len(comparison.json()["series"]) == 2
    assert client.post("/api/backtests/compare", json={"ids": [payload["id"]]}).status_code == 422
    assert (
        client.post(
            "/api/backtests/compare",
            json={"ids": [payload["id"], "does-not-exist"]},
        ).status_code
        == 404
    )

    signal = client.post("/api/signals/generate", json={"strategy_id": "balanced", "persist": True})
    assert signal.status_code == 200
    assert len(signal.json()["targets"]) == 10

    symbol = next(iter(signal.json()["targets"]))
    order = client.post(
        "/api/paper/orders",
        json={"symbol": symbol, "action": "buy", "quantity": 100},
    )
    assert order.status_code == 200
    assert order.json()["status"] == "filled"
    invalid_lot = client.post(
        "/api/paper/orders",
        json={"symbol": symbol, "action": "buy", "quantity": 50},
    )
    assert invalid_lot.status_code == 422
    assert client.get("/api/paper/orders").json()
    account = client.get("/api/paper/account?profile=demo")
    assert account.status_code == 200
    assert account.json()["account"]["equity"] > 0
    preview = client.post(
        "/api/paper/rebalance/preview",
        json={"strategy_id": "balanced", "profile": "demo"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["risk_status"] in {"ready", "blocked"}


def test_strategy_validation_contract():
    client = TestClient(app)
    strategy = client.get("/api/strategies/value")
    assert strategy.status_code == 200
    payload = strategy.json()
    assert payload["built_in"] is True
    assert payload["editable"] is False

    validated = client.post(
        "/api/strategies/validate",
        json={"yaml": payload["yaml"]},
    )
    assert validated.status_code == 200
    assert validated.json()["valid"] is True


def test_deterministic_research_run_stops_before_paper_execution(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "research.db"

    def factory():
        return ResultStore(database)

    monkeypatch.setattr(framework_service, "ResultStore", factory)
    monkeypatch.setattr(research_service, "ResultStore", factory)
    manager = research_service.ResearchRunManager()

    result = manager.run_now(
        {
            "strategy_id": "value",
            "profile": "demo",
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
            "account_id": "paper",
        }
    )

    assert result["status"] == "succeeded"
    assert all(step["status"] == "succeeded" for step in result["steps"])
    assert result["result"]["paper_execution"] == "awaiting_user_confirmation"
    store = ResultStore(database)
    try:
        assert store.list_orders(account_id="paper").empty
    finally:
        store.close()
