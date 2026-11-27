from __future__ import annotations

import sqlite3
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import StrategyRepository
from alphalab.strategy.factor_readers import FactorAssetReader
from alphalab.strategy.history import SourceHistory
from apps.api.routers import strategy
from apps.api.services import strategy_service

BASE = "/api/strategy/projects/research"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    path = tmp_path / "factor-assets.db"
    repo = StrategyRepository(path)
    repo.clone_project("sdk-v1-default", "research")
    repo.close()
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(path))
    monkeypatch.setattr(strategy_service, "_strategy_repository", lambda _strategy="main": StrategyRepository(path))
    monkeypatch.setattr(strategy_service, "factor_asset_reader", lambda: FactorAssetReader(path))
    app = FastAPI()
    app.include_router(strategy.router)
    return path, TestClient(app)


def asset(repo, path="factors/momentum_20d.py"):
    return next(item for item in repo.list_factor_assets("research") if item["path"] == path)


def evidence(repo, **overrides):
    package = repo.get_package("research")
    result = {
        "project_id": "research",
        "revision": package["revision"],
        "source_sha256": package["source_sha256"],
        "status": "insufficient",
        "summary": {"observations": 3},
        "rows": [],
    }
    payload = {
        "project_id": "research",
        "factor_id": "momentum_20d",
        "strategy_id": "main",
        "result": result,
        "request": {"start_date": "2024-01-01", "end_date": "2025-01-01", "parameters": {"window": 20}},
        "sample": {"universe": "sdk-v1-default", "start_date": "2024-01-01", "end_date": "2025-01-01"},
        "assumptions": {"forward_return": "next_open_to_open", "missing": "not_zero_filled"},
        "data_requirements": {"scope": "strategy_package"},
    }
    payload.update(overrides)
    return repo.record_factor_evidence(**payload)


def transition(client, asset_id, status, revision, evidence_ids=None, request_id=None):
    return client.post(
        BASE + f"/factor-assets/{asset_id}/transitions",
        json={
            "to_status": status,
            "reason": "Review deterministic sample and assumptions",
            "expected_status_revision": revision,
            "evidence_ids": evidence_ids or [],
            "request_id": request_id or str(uuid.uuid4()),
            "confirm_write": True,
        },
    )


def test_status_requires_current_evidence_and_is_concurrent_and_idempotent(workspace):
    path, client = workspace
    repo = StrategyRepository(path)
    current = asset(repo)
    assert transition(client, current["id"], "observation", 1).status_code == 422
    saved = evidence(repo)
    jobs_before = repo._conn.execute("SELECT * FROM backtest_jobs").fetchall()
    response = transition(client, current["id"], "observation", 1, [saved["id"]], "observe-1")
    assert response.status_code == 200, response.text
    observed = response.json()
    assert observed["asset"]["status"] == "observation"
    assert observed["asset"]["status_revision"] == 2
    assert observed["transition"]["factor_revision_id"] == current["current_revision_id"]
    assert transition(client, current["id"], "observation", 1, [saved["id"]], "observe-1").json() == observed
    adopted = transition(client, current["id"], "adopted", 2, [saved["id"]], "adopt-1")
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["asset"]["status"] == "adopted"
    stale = transition(client, current["id"], "retired", 1)
    assert stale.status_code == 409
    assert repo._conn.execute("SELECT * FROM backtest_jobs").fetchall() == jobs_before
    repo.close()


def test_definition_change_resets_adopted_but_retired_does_not_reopen(workspace):
    path, client = workspace
    repo = StrategyRepository(path)
    current = asset(repo)
    saved = evidence(repo)
    assert transition(client, current["id"], "observation", 1, [saved["id"]]).status_code == 200
    assert transition(client, current["id"], "adopted", 2, [saved["id"]]).status_code == 200
    source = repo.get_factor_source("research", "momentum_20d")["source"]
    repo.replace_factor_source("research", "momentum_20d", source.replace("] = 20,", "] = 60,"))
    changed = asset(repo)
    assert changed["id"] == current["id"]
    assert changed["status"] == "candidate"
    assert changed["current_revision_id"] != current["current_revision_id"]
    history = repo.list_factor_status_transitions("research", current["id"])
    assert history[-1]["origin"] == "system_definition_reset"
    assert history[-1]["factor_revision_id"] == changed["current_revision_id"]
    assert transition(client, current["id"], "retired", changed["status_revision"]).status_code == 200
    repo.replace_factor_source("research", "momentum_20d", source.replace("] = 20,", "] = 40,"))
    assert asset(repo)["status"] == "retired"
    assert len(repo.list_factor_evidence("research", current["id"])["items"]) == 1
    repo.close()


def test_research_persists_version_bound_evidence_and_read_apis(workspace, monkeypatch):
    path, client = workspace
    repo = StrategyRepository(path)
    current = asset(repo)
    package = repo.get_package("research")
    repo.close()
    monkeypatch.setattr(
        strategy_service,
        "evaluate_factor_research",
        lambda *args, **kwargs: {
            "project_id": "research",
            "revision": package["revision"],
            "source_sha256": package["source_sha256"],
            "status": "insufficient",
            "summary": {"observations": 0},
            "decay": [],
            "rows": [],
        },
    )
    response = client.post(
        BASE + "/factors/momentum_20d/research",
        json={
            "profile": "runtime",
            "start_date": "2024-01-01",
            "end_date": "2025-01-01",
            "frequency": "monthly",
            "quantiles": 5,
            "horizons": [1, 3, 6],
            "parameters": {"window": 20},
            "confirm_python_execution": True,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["factor_revision_id"] == current["current_revision_id"]
    assert result["factor_source_sha256"]
    assert result["data_manifest"]["status"] == "unavailable"
    evidence_id = result["evidence_id"]
    page = client.get(BASE + f"/factor-assets/{current['id']}/evidence")
    assert page.status_code == 200, page.text
    assert page.json()["items"][0]["id"] == evidence_id
    detail = client.get(BASE + f"/factor-evidence/{evidence_id}")
    assert detail.status_code == 200
    assert detail.json()["sample"]["universe"] == "research_universe"
    assert detail.json()["assumptions"]["forward_return"] == "next_open_to_open"
    assert detail.json()["result"]["status"] == "insufficient"


def test_copy_asset_directory_detail_and_usage(workspace):
    path, client = workspace
    repo = StrategyRepository(path)
    original = asset(repo)
    expected_hash = repo.get_project("research")["draft_source_sha256"]
    repo.close()
    response = client.post(
        BASE + "/factors/momentum_20d/copy",
        json={
            "new_factor_id": "momentum_copy",
            "expected_source_sha256": expected_hash,
            "confirm_write": True,
            "confirm_python_execution": True,
        },
    )
    assert response.status_code == 201, response.text
    copied = response.json()["asset"]
    assert copied["copied_from_asset_id"] == original["id"]
    assert copied["status"] == "candidate"
    labels = {
        item["id"]: item["label"]
        for item in response.json()["project"]["inspection"]["entrypoints"]
        if item["kind"] == "factor"
    }
    assert labels["momentum_copy"] == labels["momentum_20d"]
    listing = client.get(BASE + "/factor-assets")
    assert listing.status_code == 200
    assert len(listing.json()["items"]) == 2
    detail = client.get(BASE + f"/factor-assets/{original['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["asset"]["id"] == original["id"]
    assert detail.json()["usage"][0]["assembly_scope"] is True
    assert detail.json()["usage"][0]["usage"] in {"signal_direct", "signal_static_reference", "assembly_scope", "unknown_or_potential"}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM backtest_jobs").fetchall() == []


def test_asset_and_evidence_gets_do_not_change_database_bytes(workspace):
    path, client = workspace
    repo = StrategyRepository(path)
    current = asset(repo)
    saved = evidence(repo)
    repo.close()
    before = path.read_bytes()
    assert client.get(BASE + "/factor-assets").status_code == 200
    assert client.get(BASE + f"/factor-assets/{current['id']}").status_code == 200
    assert client.get(BASE + f"/factor-assets/{current['id']}/evidence").status_code == 200
    assert client.get(BASE + f"/factor-evidence/{saved['id']}").status_code == 200
    assert path.read_bytes() == before


def test_status_replay_with_different_payload_conflicts(workspace):
    path, client = workspace
    repo = StrategyRepository(path)
    current = asset(repo)
    saved = evidence(repo)
    repo.close()
    first = client.post(
        BASE + f"/factor-assets/{current['id']}/transitions",
        json={
            "to_status": "observation", "reason": "first reason",
            "expected_status_revision": 1, "evidence_ids": [saved["id"]],
            "request_id": "same-request", "confirm_write": True,
        },
    )
    assert first.status_code == 200
    conflict = client.post(
        BASE + f"/factor-assets/{current['id']}/transitions",
        json={
            "to_status": "observation", "reason": "different reason",
            "expected_status_revision": 1, "evidence_ids": [saved["id"]],
            "request_id": "same-request", "confirm_write": True,
        },
    )
    assert conflict.status_code == 409


def test_historical_revision_research_is_retained_as_historical_evidence(workspace, monkeypatch):
    path, client = workspace
    repo = StrategyRepository(path)
    old_package = repo.get_package("research", 1)
    source = repo.get_factor_source("research", "momentum_20d")["source"]
    repo.replace_factor_source("research", "momentum_20d", source.replace("] = 20,", "] = 60,"))
    current = asset(repo)
    old_revision = current["current_revision_id"] - 1
    repo.close()
    monkeypatch.setattr(
        strategy_service,
        "evaluate_factor_research",
        lambda *args, **kwargs: {
            "project_id": "research", "revision": old_package["revision"],
            "source_sha256": old_package["source_sha256"], "status": "insufficient", "rows": [],
        },
    )
    response = client.post(
        BASE + "/factors/momentum_20d/research",
        json={
            "profile": "runtime", "start_date": "2024-01-01", "end_date": "2025-01-01",
            "revision": old_package["revision"], "frequency": "monthly", "quantiles": 5,
            "horizons": [1, 3, 6], "parameters": {}, "confirm_python_execution": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["factor_revision_id"] == old_revision
    assert response.json()["sample"]["universe"]


def test_evidence_pagination_is_stable_within_one_second(workspace):
    path, client = workspace
    repo = StrategyRepository(path)
    current = asset(repo)
    expected = {evidence(repo)["id"] for _ in range(3)}
    repo.close()
    seen = set()
    before = None
    for _ in range(3):
        params = {"limit": 1}
        if before:
            params["before"] = before
        response = client.get(BASE + f"/factor-assets/{current['id']}/evidence", params=params)
        assert response.status_code == 200
        page = response.json()
        seen.update(item["id"] for item in page["items"])
        before = page["next_before"]
    assert seen == expected


def test_non_main_research_binds_actual_package_and_matching_factor_revision(workspace, monkeypatch):
    path, client = workspace
    repo = StrategyRepository(path)
    repo.create_strategy("research", "second", name="Second")
    repo.select_strategy("main")
    source = repo.get_factor_source("research", "momentum_20d")["source"]
    repo.replace_factor_source("research", "momentum_20d", source.replace("] = 20,", "] = 60,"))
    changed = asset(repo)
    second_source = repo.select_strategy("second").get_project("research")["strategy_source"]
    repo.update_strategy_source("research", second_source.replace("top_n: int = 10", "top_n: int = 3"))
    second_package = repo.select_strategy("second").get_package("research")
    repo.close()
    monkeypatch.setattr(
        strategy_service,
        "evaluate_factor_research",
        lambda *args, **kwargs: {
            "project_id": "research", "revision": second_package["revision"],
            "source_sha256": second_package["source_sha256"], "status": "insufficient", "rows": [],
        },
    )
    response = client.post(
        BASE + "/factors/momentum_20d/research?strategy_id=second",
        json={
            "profile": "runtime", "start_date": "2024-01-01", "end_date": "2025-01-01",
            "frequency": "monthly", "quantiles": 5, "horizons": [1, 3, 6],
            "parameters": {}, "confirm_python_execution": True,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["factor_revision_id"] == changed["current_revision_id"]
    saved = client.get(BASE + f"/factor-evidence/{result['evidence_id']}")
    assert saved.status_code == 200
    assert saved.json()["package_revision"] == second_package["revision"]
    assert saved.json()["package_sha256"] == second_package["source_sha256"]


def test_same_hash_restore_requires_explicit_revision_for_historical_evidence(workspace, monkeypatch):
    path, client = workspace
    repo = StrategyRepository(path)
    original = asset(repo)
    old_package = repo.get_package("research", 1)
    source = repo.get_factor_source("research", "momentum_20d")["source"]
    repo.replace_factor_source("research", "momentum_20d", source.replace("] = 20,", "] = 60,"))
    history = SourceHistory(path)
    preview = history.factor_restore_preview("research", "main", "momentum_20d", original["current_revision_id"])
    repo.restore_factor("research", "momentum_20d", original["current_revision_id"], preview["token"], "same-hash-restore")
    repo.close()
    monkeypatch.setattr(
        strategy_service,
        "evaluate_factor_research",
        lambda *args, **kwargs: {
            "project_id": "research", "revision": old_package["revision"],
            "source_sha256": old_package["source_sha256"], "status": "insufficient", "rows": [],
        },
    )
    payload = {
        "profile": "runtime", "start_date": "2024-01-01", "end_date": "2025-01-01",
        "frequency": "monthly", "quantiles": 5, "horizons": [1, 3, 6],
        "parameters": {}, "confirm_python_execution": True,
    }
    ambiguous = client.post(BASE + "/factors/momentum_20d/research", json={**payload, "revision": 1})
    assert ambiguous.status_code == 422
    response = client.post(
        BASE + "/factors/momentum_20d/research",
        json={**payload, "revision": 1, "factor_revision_id": original["current_revision_id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["factor_revision_id"] == original["current_revision_id"]
    assert response.json()["sample"]["universe"]


def test_explicit_revision_must_match_actual_factor_unit(workspace, monkeypatch):
    path, client = workspace
    repo = StrategyRepository(path)
    original = asset(repo)
    source = repo.get_factor_source("research", "momentum_20d")["source"]
    repo.replace_factor_source("research", "momentum_20d", source.replace("] = 20,", "] = 60,"))
    package = repo.get_package("research")
    repo.close()
    monkeypatch.setattr(
        strategy_service,
        "evaluate_factor_research",
        lambda *args, **kwargs: {
            "project_id": "research", "revision": package["revision"],
            "source_sha256": package["source_sha256"], "status": "insufficient", "rows": [],
        },
    )
    response = client.post(
        BASE + "/factors/momentum_20d/research",
        json={
            "profile": "runtime", "start_date": "2024-01-01", "end_date": "2025-01-01",
            "frequency": "monthly", "quantiles": 5, "horizons": [1, 3, 6],
            "parameters": {}, "factor_revision_id": original["current_revision_id"],
            "confirm_python_execution": True,
        },
    )
    assert response.status_code == 422
