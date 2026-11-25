from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab.dataio.recipes import render_builtin_recipe
from alphalab.dataio.runtime import OperationsStore
from alphalab.strategy.history import SourceHistory
from apps.api.routers import data_sync
from apps.api.services import data_sync_service

BASE = "/api/data-sync/recipes/research"


def source(template="rq.etf_daily", start="2021-08-25"):
    return render_builtin_recipe(template, start=start, end="2026-08-25")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    operations = OperationsStore(tmp_path)
    monkeypatch.setattr(data_sync_service, "get_job_manager", lambda: SimpleNamespace(operations=operations))
    monkeypatch.setattr(data_sync_service.strategy_service, "get_project", lambda _project: {"id": "research"})
    monkeypatch.setattr(
        data_sync_service,
        "source_history",
        lambda: SourceHistory(operations.path, "recipe"),
        raising=False,
    )
    app = FastAPI()
    app.include_router(data_sync.router)
    return operations, TestClient(app)


def proposal(client, revision, request_id="recipe-restore-1"):
    response = client.get(BASE + "/restore-preview", params={"revision": revision})
    assert response.status_code == 200, response.text
    return {
        "revision": revision,
        "token": response.json()["token"],
        "request_id": request_id,
        "confirm_write": True,
    }


def seed_source_restore(operations):
    current_source = source(start="2022-01-01")
    legacy = source().replace(
        "    '''Preview the sync plan and publish normalized research data in run mode.'''\n",
        "",
        1,
    )
    assert legacy != source()
    operations.save_recipe_draft("research", legacy, selected_template_id="rq.etf_daily")
    operations.save_recipe_draft("research", current_source, selected_template_id="rq.a_share_daily")
    return legacy


def test_restore_source_creates_revision_preserves_jobs_and_reopens_without_migration(workspace):
    operations, client = workspace
    legacy = seed_source_restore(operations)
    request = proposal(client, 1)
    before = operations.path.read_bytes()
    with sqlite3.connect(operations.path) as db:
        old_jobs = db.execute("SELECT * FROM sync_jobs").fetchall()
    response = client.post(BASE + "/restore", json=request)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["revision"] == 3
    assert receipt["restored_from"] == 1
    assert receipt["previous_revision"] == 2
    assert receipt["selected_template_id"] == "rq.etf_daily"
    current = operations.get_recipe_draft("research")
    assert current["source"] == legacy
    reopened = client.get(BASE)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["draft"]["source"] == legacy
    assert reopened.json()["migration"]["applied"] is False
    assert len(SourceHistory(operations.path, "recipe").list("research")["items"]) == 3
    with sqlite3.connect(operations.path) as db:
        assert db.execute("SELECT * FROM sync_jobs").fetchall() == old_jobs
        assert db.execute(
            """SELECT restored_from, previous_revision, request_id, preview_token
               FROM data_recipe_history WHERE project_id='research' AND revision=3"""
        ).fetchone() == (1, 2, request["request_id"], request["token"])
    operations.save_recipe_draft("research", source(start="2023-01-01"))
    assert client.post(BASE + "/restore", json=request).json() == receipt
    assert operations.get_recipe_draft("research")["source"] == source(start="2023-01-01")
    assert len(before) > 0


def test_template_only_restore_has_metadata_diff_and_noop_is_rejected(workspace):
    operations, client = workspace
    operations.save_recipe_draft("research", source(), selected_template_id="rq.etf_daily")
    operations.save_recipe_draft("research", source(), selected_template_id="rq.a_share_daily")
    preview = client.get(BASE + "/restore-preview", params={"revision": 1})
    assert preview.status_code == 200
    assert preview.json()["changes"] == []
    assert preview.json()["metadata_changes"] == [
        {"field": "selected_template_id", "before": "rq.a_share_daily", "after": "rq.etf_daily"}
    ]
    response = client.post(BASE + "/restore", json=proposal(client, 1))
    assert response.status_code == 200, response.text
    assert response.json()["selected_template_id"] == "rq.etf_daily"
    assert client.post(
        BASE + "/restore", json=proposal(client, 3, "noop")
    ).status_code == 422


def test_template_only_concurrent_change_invalidates_preview(workspace):
    operations, client = workspace
    operations.save_recipe_draft("research", source(), selected_template_id="rq.etf_daily")
    operations.save_recipe_draft("research", source(start="2022-01-01"), selected_template_id="rq.a_share_daily")
    request = proposal(client, 1)
    operations.save_recipe_draft(
        "research",
        source(start="2022-01-01"),
        expected_source_sha256=operations.get_recipe_draft("research")["source_sha256"],
        selected_template_id=None,
    )
    assert client.post(BASE + "/restore", json=request).status_code == 409
    assert operations.get_recipe_draft("research")["selected_template_id"] is None


def test_missing_historical_template_is_explicit_and_does_not_rewrite(workspace):
    operations, client = workspace
    operations.save_recipe_draft("research", source(), selected_template_id="custom.deleted")
    operations.save_recipe_draft("research", source(start="2022-01-01"), selected_template_id="rq.etf_daily")
    before = operations.get_recipe_draft("research")
    response = client.get(BASE + "/restore-preview", params={"revision": 1})
    assert response.status_code == 422
    assert "template" in response.text
    assert operations.get_recipe_draft("research") == before


def test_preview_is_read_only_and_does_not_open_job_manager(workspace, monkeypatch):
    operations, client = workspace
    seed_source_restore(operations)
    before = operations.path.read_bytes()

    def forbidden():
        pytest.fail("preview initialized the sync job manager")

    monkeypatch.setattr(data_sync_service, "get_job_manager", forbidden)
    proposal(client, 1)
    assert operations.path.read_bytes() == before


def test_database_failure_rolls_back_recipe_history_and_draft(workspace):
    operations, client = workspace
    seed_source_restore(operations)
    request = proposal(client, 1)
    with sqlite3.connect(operations.path) as db:
        db.execute(
            """CREATE TRIGGER reject_recipe_restore BEFORE INSERT ON data_recipe_history
               WHEN NEW.restored_from IS NOT NULL BEGIN SELECT RAISE(ABORT, 'injected'); END"""
        )
        before = [
            db.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("data_recipe_drafts", "data_recipe_history", "sync_jobs")
        ]
        db.commit()
    response = client.post(BASE + "/restore", json=request)
    assert response.status_code == 500
    with sqlite3.connect(operations.path) as db:
        after = [
            db.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("data_recipe_drafts", "data_recipe_history", "sync_jobs")
        ]
    assert after == before
