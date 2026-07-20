from __future__ import annotations

from fastapi.testclient import TestClient

from alphalab import ResultStore
from dashboard.backend.main import app
from dashboard.backend.services import framework_service


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
    assert client.get("/api/paper/orders").json()
