"""ETF option HTTP contracts and bounded numerical work."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import options

app = FastAPI()
app.include_router(options.router)
client = TestClient(app)

PAYLOAD = {
    "spot": 100,
    "strike": 100,
    "maturity": 1,
    "rate": 0.05,
    "volatility": 0.2,
    "option_type": "C",
    "dividend_yield": 0,
}


def test_bs_iv_and_mc_http_contract():
    result = client.post(
        "/api/options/price",
        json={**PAYLOAD, "market_price": 10.450583572185565, "include_mc": True, "paths": 20000},
    )
    assert result.status_code == 200
    data = result.json()
    assert data["bs_price"] == pytest.approx(10.450583572185565)
    assert data["iv"] == pytest.approx(0.2)
    assert data["mc"]["measure"] == "Q"


@pytest.mark.parametrize(
    "extra",
    [
        {"paths": 500001},
        {"volatility": -1},
        {"spot": 0},
        {"option_type": "A"},
        {"market_price": 200},
        {"unexpected": True},
    ],
)
def test_invalid_or_unbounded_work_rejected(extra):
    response = client.post("/api/options/price", json={**PAYLOAD, **extra})
    assert response.status_code == 422


def test_missing_local_chain_and_path_injection():
    response = client.get(
        "/api/options/chain",
        params={
            "underlying": "510050.XSHG",
            "day": "2025-01-06",
            "rate": 0.02,
            "dividend_yield": 0.01,
        },
    )
    assert response.status_code == 404
    response = client.get(
        "/api/options/chain",
        params={
            "underlying": "../../elsewhere",
            "day": "2025-01-06",
            "rate": 0.02,
            "dividend_yield": 0.01,
        },
    )
    assert response.status_code == 422


def test_stress_http_full_revaluation_and_contract_units():
    response = client.post(
        "/api/options/stress", json={**PAYLOAD, "multiplier": 10220, "quantity": -2, "paths": 2000}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["scenarios"]) == 8
    assert data["quantity"] == -2 and data["multiplier"] == 10220
    assert data["calibration"] == "user_supplied_scenario_parameters"


def test_sync_plan_is_visible_and_offline():
    response = client.post(
        "/api/options/sync/plan",
        json={
            "underlyings": ["510050.XSHG", "588000.XSHG"],
            "start": "2025-01-06",
            "end": "2025-01-10",
            "overlap": 1,
            "confirm_python_execution": True,
        },
    )
    assert response.status_code == 200
    report = response.json()
    assert {p["dataset"] for p in report["planned"]} == {
        "rq.option_snapshots",
        "rq.option_rates",
        "rq.option_underlyings",
        "rq.instruments", "rq.bars", "rq.paused", "rq.option_etf_5m",
        "research.option_intraday_variance", "research.option_features",
    }
    assert "OVERLAP_SESSIONS = 1" in report["source"]
    assert "rq.options.get_contract_property" in report["source"]
    assert "588000.XSHG" in report["source"]


@pytest.mark.parametrize(
    "extra",
    [
        {"confirm_python_execution": False},
        {"underlyings": ["../../elsewhere"]},
        {"underlyings": ["000001.XSHE"]},
        {"start": "2025-01-11", "end": "2025-01-06"},
        {"source": "print('arbitrary code')"},
    ],
)
def test_sync_boundary_rejects_unsupported_requests(extra):
    response = client.post(
        "/api/options/sync/plan",
        json={
            "underlyings": ["510050.XSHG"],
            "start": "2025-01-06",
            "end": "2025-01-10",
            "confirm_python_execution": True,
            **extra,
        },
    )
    assert response.status_code == 422


def test_options_jobs_reuse_existing_recipe_manager(monkeypatch):
    from apps.api.services import data_sync_service

    submitted = []

    class Manager:
        def submit_recipe(self, source, *, project_id):
            submitted.append((source, project_id))
            return {"id": "test-job", "status": "queued", "progress": 0, "total": 0}

    monkeypatch.setattr(data_sync_service, "get_job_manager", lambda: Manager())
    response = client.post(
        "/api/options/sync/jobs",
        json={
            "underlyings": ["510050.XSHG"],
            "start": "2025-01-06",
            "end": "2025-01-10",
            "confirm_python_execution": True,
        },
    )
    assert response.status_code == 202 and response.json()["id"] == "test-job"
    assert submitted[0][1] == "etf-options-workbench"
    assert "@data_recipe" in submitted[0][0]


@pytest.mark.parametrize(
    "extra",
    [
        {"paths": 250001},
        {"legs": []},
        {"correlation": -0.1},
        {"rate": "NaN"},
        {"market_price": 1.0},
        {
            "legs": [
                {
                    "underlying": "510050.XSHG",
                    "contract": "10000001",
                    "quantity": 1,
                    "multiplier": 99999,
                }
            ]
        },
    ],
)
def test_portfolio_rejects_unbounded_work_and_client_valuation_inputs(extra):
    response = client.post(
        "/api/options/portfolio",
        json={
            "day": "2025-01-06",
            "legs": [{"underlying": "510050.XSHG", "contract": "10000001", "quantity": 1}],
            **extra,
        },
    )
    assert response.status_code == 422
