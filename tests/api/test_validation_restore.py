from __future__ import annotations

import sqlite3
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import StrategyRepository
from alphalab.strategy.history import SourceHistory
from alphalab.validation.repository import ValidationRepository
from apps.api.routers import validation
from apps.api.services import validation_service

BASE = "/api/validation/projects/research"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    path = tmp_path / "validation-restore.db"
    strategies = StrategyRepository(path)
    strategies.clone_project("sdk-v1-default", "research")
    strategies.close()
    repo = ValidationRepository(path)
    current = repo.get_or_create("research")
    for value in (365, 250):
        current = repo.update_parameters(
            "research",
            [{"entrypoint_id": "performance", "parameter": "periods_per_year", "value": value}],
            expected_source_sha256=current["source_sha256"],
        )
    repo.close()
    monkeypatch.setattr(validation_service, "repository", lambda: ValidationRepository(path))
    monkeypatch.setattr(
        validation_service,
        "source_history",
        lambda: SourceHistory(path, "validation"),
        raising=False,
    )
    app = FastAPI()
    app.include_router(validation.router)
    return path, TestClient(app)


def proposal(client, revision=2, request_id="restore-validation-1"):
    response = client.get(BASE + "/restore-preview", params={"revision": revision})
    assert response.status_code == 200, response.text
    preview = response.json()
    if revision == 3:
        assert preview["changes"] == []
    else:
        assert preview["changes"][0]["path"] == "validation.py"
        assert "periods_per_year: int = 365" in preview["changes"][0]["after_source"]
    return {
        "revision": revision,
        "token": preview["token"],
        "request_id": request_id,
        "confirm_write": True,
    }


def test_restore_creates_new_revision_preserves_history_and_retries(workspace):
    path, client = workspace
    request = proposal(client)
    before = path.read_bytes()
    with sqlite3.connect(path) as db:
        db.execute(
            """INSERT INTO backtests(
                   id, config_yaml, strategy_project_id, strategy_revision, strategy_source_sha256,
                   validation_revision, validation_source_sha256
               ) SELECT 'pinned', '', 'research', 1, 'strategy', 3, source_sha256
                 FROM validation_source_packages WHERE project_id='research' AND revision=3"""
        )
        db.commit()
        old_packages = db.execute(
            "SELECT * FROM validation_source_packages ORDER BY revision"
        ).fetchall()
        old_runs = db.execute("SELECT * FROM backtests").fetchall()
        old_jobs = db.execute("SELECT * FROM backtest_jobs").fetchall()

    response = client.post(BASE + "/restore", json=request)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt == {
        "revision": 4,
        "restored_from": 2,
        "previous_revision": 3,
        "source_sha256": receipt["source_sha256"],
    }
    assert len(receipt["source_sha256"]) == 64
    repo = ValidationRepository(path)
    current = repo.get_or_create("research")
    assert current["current_revision"] == 4
    assert "periods_per_year: int = 365" in current["source"]
    assert repo.get_package("research", 2)["source_sha256"] == receipt["source_sha256"]
    repo.update_source("research", current["source"] + "\n# later edit\n")
    assert client.post(BASE + "/restore", json=request).json() == receipt
    assert "# later edit" in repo.get_or_create("research")["source"]
    repo.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM backtests").fetchall() == old_runs
        assert db.execute("SELECT * FROM backtest_jobs").fetchall() == old_jobs
        assert old_packages == [
            row for row in db.execute(
                "SELECT * FROM validation_source_packages ORDER BY revision"
            ).fetchall()
            if row[1] <= 3
        ]
        row = db.execute(
            """SELECT restored_from, previous_revision, request_id, preview_token
               FROM validation_source_packages WHERE project_id='research' AND revision=4"""
        ).fetchone()
        assert row == (2, 3, request["request_id"], request["token"])
    assert len(before) > 0


def test_preview_is_read_only_and_does_not_open_mutable_repository(workspace, monkeypatch):
    path, client = workspace
    before = path.read_bytes()

    def forbidden():
        pytest.fail("preview opened a mutable validation repository")

    monkeypatch.setattr(validation_service, "repository", forbidden)
    proposal(client)
    assert path.read_bytes() == before


def test_noop_stale_preview_and_request_id_conflict(workspace):
    path, client = workspace
    noop = proposal(client, revision=3, request_id="noop")
    assert client.post(BASE + "/restore", json=noop).status_code == 422

    request = proposal(client)
    repo = ValidationRepository(path)
    current = repo.get_or_create("research")
    repo.update_source("research", current["source"] + "\n# concurrent edit\n")
    repo.close()
    assert client.post(BASE + "/restore", json=request).status_code == 409

    conflict = client.post(
        BASE + "/restore",
        json={**request, "revision": 1},
    )
    assert conflict.status_code == 409


def test_invalid_history_and_builtin_project_fail_without_writes(workspace):
    path, client = workspace
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE validation_source_packages SET source_sha256='broken' WHERE project_id='research' AND revision=2"
        )
        db.commit()
    before = path.read_bytes()
    response = client.get(BASE + "/restore-preview", params={"revision": 2})
    assert response.status_code == 422
    assert path.read_bytes() == before
    assert client.get(
        "/api/validation/projects/sdk-v1-default/restore-preview",
        params={"revision": 1},
    ).status_code == 403


def test_database_failure_rolls_back_all_restore_writes(workspace):
    path, client = workspace
    request = proposal(client)
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TRIGGER reject_validation_restore BEFORE INSERT ON validation_source_packages
               WHEN NEW.restored_from IS NOT NULL BEGIN SELECT RAISE(ABORT, 'injected'); END"""
        )
        before = [
            db.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("validation_sources", "validation_source_packages", "backtests", "backtest_jobs")
        ]
        db.commit()
    response = client.post(BASE + "/restore", json=request)
    assert response.status_code == 500
    with sqlite3.connect(path) as db:
        after = [
            db.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("validation_sources", "validation_source_packages", "backtests", "backtest_jobs")
        ]
    assert after == before


def test_ordinary_save_race_returns_conflict_instead_of_unique_error(tmp_path, monkeypatch):
    path = tmp_path / "validation-race.db"
    strategies = StrategyRepository(path)
    strategies.clone_project("sdk-v1-default", "research")
    strategies.close()
    ready = ValidationRepository(path)
    initial = ready.get_or_create("research")
    ready.close()
    from alphalab.validation import repository as module

    original = module.inspect_validation_source
    barrier = threading.Barrier(2)

    inspected = threading.local()

    def synchronized(source):
        result = original(source)
        if "periods_per_year: int = 252" in source or getattr(inspected, "done", False):
            return result
        inspected.done = True
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(module, "inspect_validation_source", synchronized)
    outcomes = []

    def save(value):
        repo = ValidationRepository(path)
        try:
            repo.update_source(
                "research",
                initial["source"].replace("periods_per_year: int = 252", f"periods_per_year: int = {value}"),
                expected_source_sha256=initial["source_sha256"],
            )
            outcomes.append("saved")
        except RuntimeError:
            outcomes.append("conflict")
        except Exception as exc:  # pragma: no cover - diagnostic assertion below
            outcomes.append(type(exc).__name__)
        finally:
            repo.close()

    threads = [threading.Thread(target=save, args=(value,)) for value in (365, 250)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert sorted(outcomes) == ["conflict", "saved"]
