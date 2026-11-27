import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import StrategyRepository
from alphalab.strategy.history import SourceHistory
from alphalab.strategy.source import StrategySourceError
from apps.api.routers import strategy


@pytest.fixture
def example(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    repo = StrategyRepository(path)
    repo.clone_project("sdk-v1-default", "demo")
    repo.create_strategy("demo", "second", name="Second")
    repo.update_strategy_source("demo", repo.get_project("demo")["strategy_source"] + "\n# second\n")
    src = repo.get_factor_source("demo", "momentum_20d")["source"]
    assert "] = 20," in src
    repo.replace_factor_source("demo", "momentum_20d", src.replace("] = 20,", "] = 60,"))
    repo.close()
    monkeypatch.setattr(strategy, "source_history", lambda: SourceHistory(path))
    monkeypatch.setattr(strategy.strategy_service, "repository", lambda: StrategyRepository(path))
    app = FastAPI()
    app.include_router(strategy.router)
    return path, TestClient(app), "/api/strategy/projects/demo/factors/momentum_20d"


def proposed(client, url):
    revisions = client.get(url + "/revisions").json()
    revision = revisions[-1]["id"]
    preview = client.get(url + "/restore-preview", params={"revision": revision}).json()
    assert {s["id"] for s in preview["affected_strategies"]} == {"main", "second"}
    assert "+    ] = 20," in preview["changes"][0]["diff"]
    return dict(revision=revision, token=preview["token"], request_id="restore-once",
                confirm_python_execution=True, confirm_write=True)


def test_restore_is_atomic_and_retryable(example):
    path, client, url = example
    before = path.read_bytes()
    request = proposed(client, url)
    assert path.read_bytes() == before
    assert client.post(url + "/restore", json={**request, "confirm_python_execution": False}).status_code >= 400
    with sqlite3.connect(path) as db:
        packages = db.execute("SELECT * FROM strategy_source_packages ORDER BY project_id,revision").fetchall()
        db.execute("""INSERT INTO backtests(id,config_yaml,strategy_project_id,strategy_revision,strategy_source_sha256)
                      SELECT 'pinned-run','','demo',p.revision,p.source_sha256 FROM strategy_source_packages p
                      JOIN project_strategies s ON s.project_id=p.project_id AND s.current_revision=p.revision
                      WHERE s.project_id='demo' AND s.id='main'""")
        pinned = db.execute("SELECT * FROM backtests WHERE id='pinned-run'").fetchone()
        assert pinned
    response = client.post(url + "/restore", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result["packages"]) == {"main", "second"}
    assert client.get(url + "/revisions").json()[0]["restored_from"] == request["revision"]
    repo = StrategyRepository(path)
    assert "] = 20," in repo.get_factor_source("demo", "momentum_20d")["source"]
    assert {s["id"]: s["current_revision"] for s in repo.list_strategies("demo")} == result["packages"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM strategy_source_packages ORDER BY project_id,revision").fetchall() == packages
        assert db.execute("SELECT * FROM backtests").fetchall() == [pinned]
        assert db.execute("SELECT COUNT(*) FROM backtest_jobs").fetchone() == (0,)
    # A lost response retried after another save must not undo that save.
    repo.update_strategy_source("demo", repo.get_project("demo")["strategy_source"] + "\n# newer\n")
    repo.close()
    assert client.post(url + "/restore", json=request).json() == result
    assert len(client.get(url + "/revisions").json()) == 3


@pytest.mark.parametrize("failure", ["probe", "write", "conflict"])
def test_restore_failures(example, monkeypatch, failure):
    path, client, url = example
    request = proposed(client, url)
    if failure == "write":
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TRIGGER fail_restore BEFORE INSERT ON factor_source_revisions
                       WHEN NEW.request_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
    else:
        probes = []

        def probe(source, _inspection):
            probes.append(source)
            if len(probes) == 2:
                if failure == "probe":
                    raise StrategySourceError("second probe failed", phase="execute")
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE project_strategies SET name='Concurrent change' WHERE project_id='demo' AND id='second'")

        monkeypatch.setattr(StrategyRepository, "_probe_source", staticmethod(probe))
    with sqlite3.connect(path) as db:
        def snapshot():
            return [db.execute("SELECT * FROM " + table).fetchall() for table in (
                "strategy_source_units", "strategy_source_packages", "project_strategy_packages", "factor_source_revisions"
            )] + [db.execute("SELECT project_id,id,current_revision FROM project_strategies").fetchall()]

        before = snapshot()
        response = client.post(url + "/restore", json=request)
        assert response.status_code == {"probe": 422, "write": 500, "conflict": 409}[failure], response.text
        assert before == snapshot()


def test_recreated_path_cannot_use_old_identity(example):
    path, client, url = example
    repo = StrategyRepository(path)
    source = repo.get_factor_source("demo", "momentum_20d")["source"]
    source = source.replace("momentum_20d", "extra")
    repo.add_factor_source("demo", source)
    url = url.replace("momentum_20d", "extra")
    old = client.get(url + "/revisions").json()[0]["id"]
    repo.replace_factor_source("demo", "extra", source + "\n# edited\n")
    token = client.get(url + "/restore-preview", params={"revision": old}).json()["token"]
    request = dict(revision=old, token=token, request_id="old-identity", confirm_write=True, confirm_python_execution=True)
    # Rename the SDK factor, then reuse its old path.
    repo.replace_factor_source("demo", "extra", source.replace("extra", "renamed"))
    repo.add_factor_source("demo", source)
    repo.close()
    assert len(client.get(url + "/revisions").json()) == 1
    assert client.get(url + "/restore-preview", params={"revision": request["revision"]}).status_code == 404
    assert client.post(url + "/restore", json=request).status_code == 404


def test_preview_expires_when_other_strategy_changes_then_returns(example):
    path, client, url = example
    request = proposed(client, url)
    repo = StrategyRepository(path).select_strategy("second")
    source = repo.get_project("demo")["strategy_source"]
    repo.update_strategy_source("demo", source + "\n# temporary edit\n")
    repo.update_strategy_source("demo", source)
    repo.close()
    assert client.post(url + "/restore", json=request).status_code == 409


def test_existing_project_baseline_is_current_and_idempotent(example):
    path, client, url = example
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE factor_source_revisions")
        db.execute("DROP TABLE project_source_generation")
    assert client.get(url + "/revisions").status_code == 503
    for _ in range(2):
        StrategyRepository(path).close()
    revisions = client.get(url + "/revisions").json()
    assert len(revisions) == 1
    assert client.get(url + "/restore-preview", params={"revision": revisions[0]["id"]}).json()["changes"] == []
