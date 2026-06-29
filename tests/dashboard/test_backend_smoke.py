from __future__ import annotations

from alphalab import ResultStore
from fastapi.testclient import TestClient

from dashboard.backend.main import app
from dashboard.backend.services import framework_service


def test_backend_smoke_endpoints():
    client = TestClient(app)
    assert client.get("/").json()["status"] == "ok"
    assert client.get("/api/data/providers").status_code == 200
    strategies = client.get("/api/strategies")
    assert strategies.status_code == 200
    assert {item["id"] for item in strategies.json()} >= {"momentum", "balanced"}
    assert client.get("/api/system/logs").status_code == 200


def test_backtest_endpoint_handles_empty_data(tmp_path, monkeypatch):
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
    assert payload["returns"] == []
    assert payload["weights_count"] == 0
    assert payload["metrics"]["n_periods"] == 0
