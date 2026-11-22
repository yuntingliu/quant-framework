import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import StrategyRepository
from alphalab.strategy.history import SourceHistory
from alphalab.strategy.source import StrategySourceError
from apps.api.routers import strategy

BASE = "/api/strategy/projects/research/strategy-module"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    path = tmp_path / "research.db"
    repo = StrategyRepository(path)
    repo.clone_project("sdk-v1-default", "research")
    repo.create_strategy("research", "second", name="Second")
    for owner, n in [("main", 4), ("second", 3)]:
        repo.select_strategy(owner)
        source = repo.get_project("research")["strategy_source"]
        repo.update_strategy_source("research", source.replace("top_n: int = 10", f"top_n: int = {n}"))
    factor = repo.get_factor_source("research", "momentum_20d")["source"]
    repo.replace_factor_source("research", "momentum_20d", factor.replace("] = 20,", "] = 60,"))
    repo.close()
    monkeypatch.setattr(strategy, "source_history", lambda: SourceHistory(path))
    monkeypatch.setattr(strategy.strategy_service, "repository", lambda: StrategyRepository(path))
    app = FastAPI()
    app.include_router(strategy.router)
    return path, TestClient(app)


def proposal(client, owner):
    query = {"strategy_id": owner}
    rows = client.get(BASE + "/revisions", params=query).json()
    revision = rows[-1]["id"]
    data = client.get(BASE + "/restore-preview", params={**query, "revision": revision}).json()
    assert [c["path"] for c in data["changes"]] == [f"strategies/{owner}.py"]
    assert [s["id"] for s in data["affected_strategies"]] == [owner]
    assert "top_n: int = 10" in data["changes"][0]["diff"]
    return dict(revision=revision, token=data["token"], request_id="restore-1",
                confirm_write=True, confirm_python_execution=True)


@pytest.mark.parametrize("owner,other", [("main", "second"), ("second", "main")])
def test_restore_only_selected_module(workspace, owner, other):
    path, client = workspace
    query = {"strategy_id": owner}
    request = proposal(client, owner)
    with sqlite3.connect(path) as db:
        db.execute("""INSERT INTO backtests(id,config_yaml,strategy_project_id,strategy_revision,strategy_source_sha256)
                      SELECT 'pinned','','research',p.revision,p.source_sha256 FROM strategy_source_packages p
                      JOIN project_strategies s ON s.project_id=p.project_id AND s.current_revision=p.revision
                      WHERE s.project_id='research' AND s.id=?""", (owner,))
        db.commit()

        def preserved():
            return [db.execute(sql, args).fetchall() for sql, args in [
                ("SELECT * FROM project_strategies WHERE project_id='research' AND id=?", (other,)),
                ("SELECT * FROM strategy_module_revisions WHERE project_id='research' AND strategy_id=?", (other,)),
                ("SELECT * FROM strategy_source_units WHERE kind='factor' ORDER BY project_id,path", ()),
                ("SELECT * FROM factor_source_revisions", ()), ("SELECT * FROM backtests", ()),
                ("SELECT * FROM backtest_jobs", ()),
            ]]

        before = preserved()
        old_packages = db.execute("SELECT * FROM strategy_source_packages ORDER BY project_id,revision").fetchall()
        response = client.post(BASE + "/restore", json=request, params=query)
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert preserved() == before
        assert old_packages == [r for r in db.execute("SELECT * FROM strategy_source_packages ORDER BY project_id,revision")
                                if r[:2] in {p[:2] for p in old_packages}]
    repo = StrategyRepository(path).select_strategy(owner)
    updated = repo.get_project("research")
    assert "top_n: int = 10" in updated["strategy_source"]
    assert "] = 60," in updated["draft_source"]
    assert receipt["packages"] == {owner: updated["current_revision"]}
    assert receipt["revision"] != request["revision"]
    assert receipt["restored_from"] == request["revision"]
    latest = client.get(BASE + "/revisions", params=query).json()[0]
    assert (latest["id"], latest["restored_from"]) == (receipt["revision"], request["revision"])
    repo.update_strategy_source("research", updated["strategy_source"] + "\n# later edit\n")
    assert client.post(BASE + "/restore", json=request, params=query).json() == receipt
    assert "# later edit" in repo.get_project("research")["strategy_source"]
    repo.close()


@pytest.mark.parametrize("failure,status", [("probe", 422), ("write", 500)])
def test_failure_rolls_back(workspace, monkeypatch, failure, status):
    path, client = workspace
    request = proposal(client, "main")
    with sqlite3.connect(path) as db:
        def snapshot():
            return [db.execute("SELECT * FROM " + table).fetchall() for table in (
                "strategy_projects", "project_strategies", "strategy_source_units", "strategy_source_packages",
                "strategy_source_package_units", "project_strategy_packages", "strategy_module_revisions",
                "factor_source_revisions", "backtests", "backtest_jobs",
            )]

        if failure == "write":
            db.execute("""CREATE TRIGGER inject BEFORE INSERT ON strategy_module_revisions
                          WHEN NEW.request_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'injected'); END""")
        else:
            def probe(*_):
                raise StrategySourceError("invalid for current factor library", phase="execute")
            monkeypatch.setattr(StrategyRepository, "_probe_source", staticmethod(probe))
        old = snapshot()
        response = client.post(BASE + "/restore?strategy_id=main", json=request)
        assert response.status_code == status, response.text
        assert snapshot() == old


def test_factor_edit_during_probe_wins(workspace, monkeypatch):
    path, client = workspace
    request = proposal(client, "second")
    original_probe = StrategyRepository._probe_source

    def save_concurrently(source, inspection):
        original_probe(source, inspection)
        monkeypatch.setattr(StrategyRepository, "_probe_source", staticmethod(original_probe))
        other = StrategyRepository(path)
        current = other.get_factor_source("research", "momentum_20d")["source"]
        other.replace_factor_source("research", "momentum_20d", current.replace("] = 60,", "] = 40,"))
        other.close()

    monkeypatch.setattr(StrategyRepository, "_probe_source", staticmethod(save_concurrently))
    assert client.post(BASE + "/restore?strategy_id=second", json=request).status_code == 409
    repo = StrategyRepository(path).select_strategy("second")
    assert "top_n: int = 3" in repo.get_project("research")["strategy_source"]
    assert "] = 40," in repo.get_factor_source("research", "momentum_20d")["source"]
    assert all(r["restored_from"] is None for r in client.get(BASE + "/revisions?strategy_id=second").json())
    repo.close()


def test_preview_read_only_and_owner_scoped(workspace, monkeypatch):
    path, client = workspace
    before = path.read_bytes()

    def forbidden():
        pytest.fail("read-only preview opened a mutable repository")

    monkeypatch.setattr(strategy.strategy_service, "repository", forbidden)
    request = proposal(client, "second")
    assert client.get(BASE + "/restore-preview", params={"strategy_id": "main", "revision": request["revision"]}).status_code == 404
    for flag in ("confirm_write", "confirm_python_execution"):
        assert client.post(BASE + "/restore?strategy_id=second", json={**request, flag: False}).status_code >= 400
    assert path.read_bytes() == before


def test_legacy_backfill_keeps_owned_packages_and_is_repeatable(workspace):
    path, client = workspace
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE strategy_module_revisions")
        db.commit()
        expected = sorted(r[0] for r in db.execute(
            "SELECT revision FROM project_strategy_packages WHERE project_id='research' AND strategy_id='second'"))
    for _ in range(2):
        StrategyRepository(path).close()
    rows = client.get(BASE + "/revisions?strategy_id=second").json()
    assert sorted(r["package_revision"] for r in rows) == expected
    assert all(r["restored_from"] is None for r in rows)
