"""The HTTP doors: a direct call cannot skip the rules, and stale previews are refused."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import ResultStore, create_default_engine
from apps.api.routers import paper
from apps.api.services import result_service


@pytest.fixture
def client(tmp_path, monkeypatch):
    database = tmp_path / "paper.db"
    monkeypatch.setattr(result_service, "ResultStore", lambda: ResultStore(database))
    monkeypatch.setattr(result_service, "_project_max_weight", lambda project_id: 1.0)

    engine = create_default_engine()
    symbols = engine.get_symbols()[:3]
    latest = engine.get_latest_date()
    store = ResultStore(database)
    store.ensure_backtest_subject("sdk-v1-default", "projects/sdk-v1-default")
    signal_id = store.save_signal(
        "sdk-v1-default",
        latest,
        {symbol: 0.05 for symbol in symbols},
        status="pending",
        profile="demo",
    )
    store.close()

    app = FastAPI()
    app.include_router(paper.router)
    return SimpleNamespace(
        api=TestClient(app),
        database=database,
        symbols=symbols,
        signal_id=signal_id,
    )


def account(client) -> dict:
    response = client.api.get("/api/paper/account", params={"account_id": "paper", "profile": "demo"})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_direct_api_order_cannot_bypass_the_hard_rules(client):
    before = account(client)
    response = client.api.post(
        "/api/paper/orders",
        json={
            "symbol": client.symbols[0],
            "action": "buy",
            "quantity": 200_000,
            "profile": "demo",
            "account_id": "paper",
        },
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["blocking"]
    assert detail["policy"] == "default v1"
    assert any(item["rule"] == "max_order_value" for item in detail["blocking"])

    after = account(client)
    assert after["account"]["cash"] == before["account"]["cash"]
    assert after["positions"] == before["positions"]


def test_execute_requires_the_preview_and_refuses_a_stale_one(client):
    preview = client.api.post(
        "/api/paper/rebalance/preview",
        json={"signal_id": client.signal_id, "profile": "demo", "account_id": "paper"},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["allowed"] is True

    missing = client.api.post(
        "/api/paper/rebalance/execute",
        json={"signal_id": client.signal_id, "profile": "demo", "account_id": "paper", "confirm": True},
    )
    assert missing.status_code == 422, missing.text

    stale = client.api.post(
        "/api/paper/rebalance/execute",
        json={
            "signal_id": client.signal_id,
            "profile": "demo",
            "account_id": "paper",
            "confirm": True,
            "preview_id": "an-old-approval",
        },
    )
    assert stale.status_code == 409, stale.text
    assert "out of date" in str(stale.json()["detail"])

    filled = client.api.post(
        "/api/paper/rebalance/execute",
        json={
            "signal_id": client.signal_id,
            "profile": "demo",
            "account_id": "paper",
            "confirm": True,
            "preview_id": body["preview_id"],
        },
    )
    assert filled.status_code == 200, filled.text
    assert filled.json()["status"] == "filled"
    assert filled.json()["orders_created"] == len(body["orders"])

    replay = client.api.post(
        "/api/paper/rebalance/execute",
        json={
            "signal_id": client.signal_id,
            "profile": "demo",
            "account_id": "paper",
            "confirm": True,
            "preview_id": body["preview_id"],
        },
    )
    assert replay.status_code == 409, replay.text


def test_policy_versions_are_readable_and_change_the_verdict(client):
    first = client.api.get("/api/paper/risk/policy")
    assert first.status_code == 200
    assert first.json()["active"]["version"] == 1

    published = client.api.post(
        "/api/paper/risk/policy",
        json={
            "reason": "Tighten the order cap",
            "limits": {"max_order_value": 1_000.0},
            "account_id": "paper",
        },
    )
    assert published.status_code == 200, published.text
    assert published.json()["active"]["version"] == 2

    history = client.api.get("/api/paper/risk/policy").json()
    assert [item["version"] for item in history["versions"]] == [1, 2]
    assert history["versions"][0]["reason"].startswith("Baseline")

    blocked = client.api.post(
        "/api/paper/rebalance/preview",
        json={"signal_id": client.signal_id, "profile": "demo", "account_id": "paper"},
    )
    assert blocked.json()["allowed"] is False
    assert blocked.json()["policy"]["version"] == 2


def test_a_bad_policy_payload_is_a_readable_422(client):
    response = client.api.post(
        "/api/paper/risk/policy",
        json={"reason": "", "limits": {"max_order_value": 1.0}},
    )
    assert response.status_code == 422

    typo = client.api.post(
        "/api/paper/risk/policy",
        json={"reason": "typo", "limits": {"max_order_valu": 1.0}},
    )
    assert typo.status_code == 422
    assert "max_order_valu" in typo.text


def test_holdings_risk_and_the_audit_are_exposed(client):
    preview = client.api.post(
        "/api/paper/rebalance/preview",
        json={"signal_id": client.signal_id, "profile": "demo", "account_id": "paper"},
    ).json()
    client.api.post(
        "/api/paper/rebalance/execute",
        json={
            "signal_id": client.signal_id,
            "profile": "demo",
            "account_id": "paper",
            "confirm": True,
            "preview_id": preview["preview_id"],
        },
    )

    analysis = client.api.post(
        "/api/paper/risk/analysis",
        json={"account_id": "paper", "profile": "demo"},
    )
    assert analysis.status_code == 200, analysis.text
    body = analysis.json()
    assert body["input_hash"]
    assert body["portfolio"]["evaluated"] is True
    assert body["disclaimer"]
    assert body["assumptions"]["lookback_days"] >= 60

    audit = client.api.get("/api/paper/risk/evaluations", params={"account_id": "paper"})
    assert audit.status_code == 200
    doors = [item["door"] for item in audit.json()]
    assert "rebalance_preview" in doors
    assert all(item["snapshot_id"] for item in audit.json())
