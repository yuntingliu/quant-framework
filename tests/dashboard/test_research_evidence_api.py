from __future__ import annotations

from fastapi.testclient import TestClient

from dashboard.backend.main import app
from dashboard.backend.routers import backtests as backtests_router
from dashboard.backend.services import strategy_service, validation_service


def test_factor_research_route_validates_and_forwards_the_evidence_contract(
    monkeypatch,
) -> None:
    captured = {}

    def evaluate(project_id, factor_id, **kwargs):
        captured.update({"project_id": project_id, "factor_id": factor_id, **kwargs})
        return {"status": "insufficient", "periods": 10, "rows": []}

    monkeypatch.setattr(strategy_service, "factor_research", evaluate)
    client = TestClient(app)
    response = client.post(
        "/api/strategy/projects/research/factors/value/research",
        json={
            "profile": "runtime",
            "start_date": "2024-01-01",
            "end_date": "2025-01-01",
            "frequency": "monthly",
            "quantiles": 5,
            "horizons": [1, 3, 6],
            "parameters": {},
            "confirm_python_execution": True,
        },
    )

    assert response.status_code == 200
    assert captured["project_id"] == "research"
    assert captured["factor_id"] == "value"
    assert captured["horizons"] == [1, 3, 6]
    invalid = client.post(
        "/api/strategy/projects/research/factors/value/research",
        json={
            "profile": "runtime",
            "start_date": "2024-01-01",
            "end_date": "2025-01-01",
            "horizons": [3, 6],
            "confirm_python_execution": True,
        },
    )
    assert invalid.status_code == 422


def test_backtest_validation_route_reads_the_frozen_payload(monkeypatch) -> None:
    frozen = {
        "id": "bt-1",
        "available_analyses": ["risk"],
        "outputs": {"risk": {"status": "sufficient"}},
        "warnings": [],
    }
    monkeypatch.setattr(backtests_router, "analyze_validation", lambda _id: frozen)

    response = TestClient(app).get("/api/backtests/bt-1/validation")

    assert response.status_code == 200
    assert response.json() == frozen


def test_validation_template_migration_requires_confirmation_and_hash(monkeypatch) -> None:
    captured = {}

    def migrate(project_id, *, expected_source_sha256):
        captured.update(
            {"project_id": project_id, "expected_source_sha256": expected_source_sha256}
        )
        return {"project_id": project_id, "current_revision": 3}

    monkeypatch.setattr(validation_service, "migrate_to_default", migrate)
    client = TestClient(app)
    digest = "a" * 64
    denied = client.post(
        "/api/validation/projects/research/migrate-default",
        json={"expected_source_sha256": digest, "confirm_write": False},
    )
    accepted = client.post(
        "/api/validation/projects/research/migrate-default",
        json={"expected_source_sha256": digest, "confirm_write": True},
    )

    assert denied.status_code == 409
    assert accepted.status_code == 200
    assert captured == {
        "project_id": "research",
        "expected_source_sha256": digest,
    }
