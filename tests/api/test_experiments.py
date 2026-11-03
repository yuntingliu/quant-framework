import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab import StrategyRepository, create_runtime_engine
from alphalab.dataio.runtime import RuntimeStore
from alphalab.store import ResultStore
from alphalab.strategy.source import update_parameter_default
from alphalab.validation.repository import ValidationRepository
from apps.api.routers import experiments
from apps.api.services import strategy_service
from apps.api.services.backtest_job_service import BacktestJobManager
from apps.api.services.experiment_service import ExperimentManager


def test_experiment_with_real_runtime_engine(setup_experiment, tmp_path, monkeypatch):
    repository, spec = setup_experiment
    root = tmp_path / "runtime"
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(root))
    store = RuntimeStore(root)
    dates = pd.bdate_range("2023-11-01", "2024-06-28")
    prices = pd.Series([10 + i * 0.02 + (i % 7) * 0.01 for i in range(len(dates))])
    bars = pd.DataFrame({"date": dates, "symbol": "000001.SZ"})
    for field in ("open", "high", "low", "close", "raw_open", "raw_high", "raw_low", "raw_close"):
        bars[field] = prices.to_numpy()
    bars["volume"] = 100_000_000.0
    bars["amount"] = bars["close"] * bars["volume"]
    bars["limit_up"] = bars["close"] * 1.1
    bars["limit_down"] = bars["close"] * 0.9
    store.write("rq.bars", bars)
    store.write("rq.paused", bars[["date", "symbol"]].assign(paused=False))
    store.write("rq.is_st", bars[["date", "symbol"]].assign(is_st=False))
    store.write("rq.instruments", pd.DataFrame([{
        "symbol": "000001.SZ", "asset_type": "CS", "listed_date": "2000-01-01",
        "de_listed_date": "2999-12-31", "exchange": "XSHE", "board_type": "MainBoard",
        "round_lot": 100, "snapshot_date": "2023-11-01",
    }]))
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(repository.path))
    monkeypatch.setattr(strategy_service, "ResultStore", lambda: ResultStore(repository.path))
    monkeypatch.setattr(strategy_service, "_engine", lambda profile: create_runtime_engine(root))
    monkeypatch.setattr(strategy_service, "RuntimeStore", lambda: RuntimeStore(root))
    spec["validation"] = {"start": "2024-01-01", "end": "2024-03-29"}
    spec["test"] = {"start": "2024-04-01", "end": "2024-06-28"}
    experiment = repository.create_experiment(spec)
    jobs = BacktestJobManager(repository.path)
    manager = ExperimentManager(repository.path, jobs)
    try:
        manager.start(experiment["id"])
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            state = repository.get_experiment(experiment["id"])
            if state["execution"]["status"] != "running":
                break
            time.sleep(0.05)
        assert state["execution"]["status"] == "succeeded", state
        assert [trial["stage"] for trial in state["trials"]] == ["validation", "validation", "test"]
        assert all(trial["status"] == "succeeded" for trial in state["trials"])
        assert state["trials"][-1]["candidate_id"] == state["execution"]["selection"]["candidate_id"]
        assert state["execution"]["selection"]["candidate_id"] == "1"
        results = ResultStore(repository.path)
        try:
            for trial in state["trials"]:
                record = results.get_backtest_record(trial["result_id"])
                assert record is not None
                assert record["strategy_project_id"] == spec["project_id"]
                candidate = next(item for item in experiment["candidates"] if item["candidate_id"] == trial["candidate_id"])
                package = repository.get_package(spec["project_id"], candidate["revision"])
                assert record["strategy_revision"] == candidate["revision"]
                assert record["strategy_source_sha256"] == package["source_sha256"]
                assert record["validation_revision"] == spec["validation_revision"]
                returns = results.load_returns(trial["result_id"])
                assert not returns.empty
                assert returns.index.min() >= pd.Timestamp(trial["start_date"])
                assert returns.index.max() <= pd.Timestamp(trial["end_date"])
        finally:
            results.close()
        assert manager.start(experiment["id"])["trials"] == state["trials"]
        assert repository._conn.execute("SELECT COUNT(*) FROM backtest_jobs").fetchone()[0] == 3
        assert state["execution"]["out_of_sample_verified"] is False
    finally:
        manager.shutdown()
        jobs.shutdown()


@pytest.mark.parametrize("state", ["none", "recorded", "active", "terminal", "failed"])
def test_project_deletion_lifecycle(setup_experiment, monkeypatch, state):
    from apps.api.routers import strategy
    from apps.api.services import strategy_service

    repository, spec = setup_experiment
    if state != "none":
        identity = repository.create_experiment(spec)["id"]
        if state == "failed":
            repository.start_experiment(identity)
            repository.fail_experiment(identity, "stopped before any result")
        if state in {"active", "terminal"}:
            repository.start_experiment(identity)
            job = repository.advance_experiment(identity)
            if state == "terminal":
                while job:
                    complete_trial(repository, job, 1.0)
                    job = repository.advance_experiment(identity)
    before = list(repository._conn.iterdump())
    monkeypatch.setattr(strategy_service, "repository", lambda: StrategyRepository(repository.path))
    app = FastAPI()
    app.include_router(strategy.router)
    with TestClient(app) as client:
        assert client.delete(f"/api/strategy/projects/{spec['project_id']}").status_code == 409
        assert list(repository._conn.iterdump()) == before
        response = client.delete(f"/api/strategy/projects/{spec['project_id']}?confirm_delete=true")
    if state in {"none", "recorded", "failed"}:
        assert response.status_code == 204, response.text
        assert repository.get_project(spec["project_id"]) is None
        for table in ("research_experiments", "experiment_candidates", "experiment_trials", "experiment_execution"):
            assert repository._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert repository._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    else:
        assert response.status_code == 409, response.text
        assert ("active work" if state == "active" else "Report references cannot be verified") in response.json()["detail"]
        assert list(repository._conn.iterdump()) == before
        with pytest.raises(PermissionError):
            repository.delete_project(spec["project_id"])
        assert not repository._conn.in_transaction
        assert repository._conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("status", ["queued", "running", "failed", "interrupted"])
def test_project_deletion_checks_standalone_jobs(setup_experiment, status):
    repository, spec = setup_experiment
    repository._conn.execute(
        "INSERT INTO backtest_jobs(id, status, request_json) VALUES ('standalone', ?, ?)",
        (status, json.dumps({"project_id": spec["project_id"]})),
    )
    repository._conn.commit()
    if status in {"queued", "running"}:
        with pytest.raises(PermissionError, match=f"job standalone \\({status}\\)"):
            repository.delete_project(spec["project_id"])
    else:
        assert repository.delete_project(spec["project_id"])
    assert repository._conn.execute("SELECT status FROM backtest_jobs").fetchone()[0] == status


def test_project_deletion_rolls_back_cleanup(setup_experiment):
    repository, spec = setup_experiment
    repository.create_experiment(spec)
    repository._conn.execute(
        """CREATE TRIGGER fail_delete BEFORE DELETE ON strategy_projects
           BEGIN SELECT RAISE(ABORT, 'injected deletion failure'); END"""
    )
    before = list(repository._conn.iterdump())
    with pytest.raises(sqlite3.IntegrityError, match="injected deletion failure"):
        repository.delete_project(spec["project_id"])
    assert list(repository._conn.iterdump()) == before
    assert not repository._conn.in_transaction


def test_project_deletion_blocks_shared_source(setup_experiment):
    repository, spec = setup_experiment
    experiment = repository.create_experiment(spec)
    repository.clone_project("sdk-v1-default", "other-project")
    repository._conn.execute(
        "UPDATE research_experiments SET project_id = 'other-project' WHERE id = ?", (experiment["id"],)
    )
    repository._conn.commit()
    before = list(repository._conn.iterdump())
    with pytest.raises(PermissionError, match=experiment["id"]):
        repository.delete_project(spec["project_id"])
    assert list(repository._conn.iterdump()) == before


def test_project_deletion_checks_unreconciled_result(setup_experiment):
    repository, spec = setup_experiment
    identity = repository.create_experiment(spec)["id"]
    repository.start_experiment(identity)
    job = repository.advance_experiment(identity)
    complete_trial(repository, job, 1.0)
    repository.fail_experiment(identity, "stopped before reconciliation")
    with pytest.raises(PermissionError, match="Report references cannot be verified"):
        repository.delete_project(spec["project_id"])


@pytest.fixture
def setup_experiment(tmp_path):
    path = tmp_path / "experiments.db"
    repository = StrategyRepository(path)
    repository.clone_project("sdk-v1-default", "experiment-project")
    validation = ValidationRepository(path)
    validation.get_or_create("experiment-project")
    validation.close()
    spec = {
        "project_id": "experiment-project", "base_revision": 1, "validation_revision": 1,
        "candidates": [{"momentum_20d": {"window": 10}}, {"momentum_20d": {"window": 30}}],
        "validation": {"start": "2024-01-01", "end": "2024-12-31"},
        "test": {"start": "2025-01-01", "end": "2025-12-31"}, "max_trials": 3,
    }
    yield repository, spec
    repository.close()


def test_experiment_freezes_settings_sources_and_survives_reopen(setup_experiment):
    repository, spec = setup_experiment
    before = repository.get_project(spec["project_id"])
    result = repository.create_experiment(spec)
    assert repository.get_project(spec["project_id"]) == before
    assert len(result["candidates"]) == len(result["trials"]) == 2
    assert {trial["stage"] for trial in result["trials"]} == {"validation"}
    assert {trial["status"] for trial in result["trials"]} == {"planned"}
    assert result["protocol"]["data_snapshot_status"] == "missing"
    assert result["protocol"]["execution_status"] == "not_scheduled"
    assert repository._conn.execute("SELECT COUNT(*) FROM backtest_jobs").fetchone()[0] == 0
    changed, _ = update_parameter_default(
        before["draft_source"], entrypoint_id="momentum_20d", parameter="window", value=50
    )
    repository.update_draft(spec["project_id"], changed)
    repository._conn.execute(
        "UPDATE strategy_projects SET settings_json = '{}' WHERE id = ?", (spec["project_id"],)
    )
    repository._conn.commit()
    reopened = StrategyRepository(repository.path)
    try:
        assert reopened.get_experiment(result["id"]) == result
        assert reopened.list_experiments(spec["project_id"]) == [result]
        for candidate in result["candidates"]:
            package = reopened.get_package(spec["project_id"], candidate["revision"])
            assert package["parameters"]["momentum_20d"] == candidate["parameters"]["momentum_20d"]
            assert package["parent_revision"] == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("failure", ["invalid_second_candidate", "trial_insert"])
def test_experiment_creation_rolls_back_all_records(setup_experiment, failure):
    repository, spec = setup_experiment
    before = repository.list_packages(spec["project_id"])
    if failure == "invalid_second_candidate":
        spec["candidates"][1]["momentum_20d"]["window"] = 1
    else:
        repository._conn.execute(
            """CREATE TRIGGER fail_trial BEFORE INSERT ON experiment_trials
               WHEN NEW.candidate_id = '2' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"""
        )
    with pytest.raises(Exception, match="below its minimum|injected failure"):
        repository.create_experiment(spec)
    assert repository.list_packages(spec["project_id"]) == before
    for table in ("research_experiments", "experiment_candidates", "experiment_trials"):
        assert repository._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("change", [
    {"test": {"start": "2024-12-31", "end": "2025-12-31"}},
    {"training": {"start": "2023-01-01", "end": "2024-01-01"}},
    {"validation": {"start": "2024-12-31", "end": "2024-01-01"}},
    {"max_trials": 2}, {"max_concurrency": 2}, {"selection_metric": "unknown"},
])
def test_experiment_rejects_invalid_protocol_before_writing(setup_experiment, change):
    repository, spec = setup_experiment
    with pytest.raises(ValueError):
        repository.create_experiment({**spec, **change})
    assert repository.list_experiments(spec["project_id"]) == []
    assert len(repository.list_packages(spec["project_id"])) == 1


def test_experiment_queries_preserve_failed_and_cancelled_trials(setup_experiment):
    repository, spec = setup_experiment
    result = repository.create_experiment(spec)
    for trial, status in zip(result["trials"], ("failed", "cancelled"), strict=True):
        repository._conn.execute(
            "UPDATE experiment_trials SET status = ?, error = ? WHERE id = ?",
            (status, "test fixture outcome", trial["id"]),
        )
    repository._conn.commit()
    recorded = repository.get_experiment(result["id"])
    assert [trial["status"] for trial in recorded["trials"]] == ["failed", "cancelled"]
    assert all(trial["error"] == "test fixture outcome" for trial in recorded["trials"])


def test_experiments_reuse_identical_packages_without_sharing_trial_records(setup_experiment):
    repository, spec = setup_experiment
    first = repository.create_experiment(spec)
    second = repository.create_experiment(spec)
    assert first["id"] != second["id"]
    assert [item["revision"] for item in first["candidates"]] == [item["revision"] for item in second["candidates"]]
    assert {item["id"] for item in first["trials"]}.isdisjoint(item["id"] for item in second["trials"])
    assert len(repository.list_packages(spec["project_id"])) == 3


def test_experiment_api_requires_confirmation_and_exposes_records(setup_experiment, monkeypatch):
    repository, spec = setup_experiment
    monkeypatch.setattr(experiments, "StrategyRepository", lambda: StrategyRepository(repository.path))
    app = FastAPI()
    app.include_router(experiments.router)
    with TestClient(app) as client:
        assert client.post("/api/experiments", json=spec).status_code == 409
        assert repository.list_experiments(spec["project_id"]) == []
        payload = {**spec, "confirm_python_execution": True, "data_manifest_id": "future-snapshot"}
        response = client.post("/api/experiments", json=payload)
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["protocol"]["data_snapshot_status"] == "unverified"
        assert client.get(f"/api/experiments/{result['id']}").json() == result
        assert client.get("/api/experiments", params={"project_id": spec["project_id"]}).json() == [result]
        assert client.get("/api/experiments/missing").status_code == 404
        assert client.post("/api/experiments", json={**payload, "validation_revision": 999}).status_code == 404
        invalid = deepcopy(payload)
        invalid["candidates"][1]["momentum_20d"]["window"] = 1
        assert client.post("/api/experiments", json=invalid).status_code == 422
        duplicate = {**payload, "candidates": [spec["candidates"][0]] * 2}
        assert client.post("/api/experiments", json=duplicate).status_code == 422


def complete_trial(repository, job_id, score, *, reliable=True):
    result = {
        "metrics": {"sharpe": score}, "execution_reliable": reliable,
        "research_invalid_reasons": [],
    }
    with repository._conn:
        repository._conn.execute("INSERT INTO backtests(id, config_yaml) VALUES (?, '')", (job_id,))
        repository._conn.execute(
            "UPDATE backtest_jobs SET status = 'succeeded', result_id = ?, result_json = ? WHERE id = ?",
            (job_id, json.dumps(result), job_id),
        )


def test_serial_progression_pins_selection_and_never_repeats_test(setup_experiment):
    repository, spec = setup_experiment
    experiment = repository.create_experiment(spec)
    identity = experiment["id"]
    repository.start_experiment(identity)
    first = repository.advance_experiment(identity)
    assert repository.advance_experiment(identity) == first
    assert len(repository.get_experiment(identity)["trials"]) == 2
    complete_trial(repository, first, 2.0)
    second = repository.advance_experiment(identity)
    complete_trial(repository, second, 1.0)
    test = repository.advance_experiment(identity)
    state = repository.get_experiment(identity)
    assert state["execution"]["selection"]["candidate_id"] == "1"
    assert len(state["trials"]) == 3
    request = json.loads(repository._conn.execute("SELECT request_json FROM backtest_jobs WHERE id = ?", (test,)).fetchone()[0])
    assert request["start_date"] == spec["test"]["start"]
    assert request["revision"] == experiment["candidates"][0]["revision"]
    assert request["settings"] == experiment["protocol"]["settings"]
    complete_trial(repository, test, -100.0)
    assert repository.advance_experiment(identity) is None
    assert repository.start_experiment(identity)["execution"]["status"] == "succeeded"
    repository.fail_experiment(identity, "late worker error")
    assert repository.get_experiment(identity)["execution"]["status"] == "succeeded"
    assert repository.advance_experiment(identity) is None
    assert repository._conn.execute("SELECT COUNT(*) FROM backtest_jobs").fetchone()[0] == 3
    assert repository.get_experiment(identity)["execution"]["selection"] == state["execution"]["selection"]
    assert repository.get_experiment(identity)["execution"]["out_of_sample_verified"] is False


@pytest.mark.parametrize("scores,reliable,direction,winner", [
    ([1.0, 1.0], True, "maximize", "1"),
    ([1.0, 2.0], True, "minimize", "1"),
    ([float("nan"), 2.0], True, "maximize", "2"),
    ([1.0, 2.0], False, "maximize", None),
])
def test_selection_handles_ties_nonfinite_and_invalid_evidence(setup_experiment, scores, reliable, direction, winner):
    repository, spec = setup_experiment
    spec["selection_direction"] = direction
    identity = repository.create_experiment(spec)["id"]
    repository.start_experiment(identity)
    for score in scores:
        complete_trial(repository, repository.advance_experiment(identity), score, reliable=reliable)
    job_id = repository.advance_experiment(identity)
    state = repository.get_experiment(identity)
    assert state["execution"]["selection"]["candidate_id"] == winner
    assert (job_id is None) == (winner is None)
    assert len(state["trials"]) == (2 if winner is None else 3)


def test_restart_and_concurrent_progression_reuse_durable_job(setup_experiment):
    repository, spec = setup_experiment
    identity = repository.create_experiment(spec)["id"]
    repository.start_experiment(identity)
    first = repository.advance_experiment(identity)

    def advance():
        other = StrategyRepository(repository.path)
        try:
            other.start_experiment(identity)
            return other.advance_experiment(identity)
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: advance(), range(2))) == [first, first]
    assert repository._conn.execute("SELECT COUNT(*) FROM backtest_jobs").fetchone()[0] == 1
    complete_trial(repository, first, 1.0)
    assert advance() != first


def test_serial_manager_preserves_failed_trial_and_runs_only_winner_test(setup_experiment, monkeypatch):
    repository, spec = setup_experiment
    experiment = repository.create_experiment(spec)
    calls = []

    def runner(project_id, start_date, end_date, profile, revision, validation_revision=None,
               backtest_id=None, settings=None, strategy_id=None, strategy_name=None, batch_id=None):
        calls.append((start_date, revision, settings, validation_revision))
        if revision == experiment["candidates"][0]["revision"]:
            raise ValueError("candidate failed")
        with sqlite3.connect(repository.path) as connection:
            connection.execute("INSERT INTO backtests(id, config_yaml) VALUES (?, '')", (backtest_id,))
        return {
            "id": backtest_id, "metrics": {"sharpe": 1.0}, "execution_reliable": True,
            "research_assessment": {"passed": True, "reasons": []},
        }

    jobs = BacktestJobManager(repository.path, runner=runner)
    manager = ExperimentManager(repository.path, jobs)
    monkeypatch.setattr(experiments.experiment_service, "manager", lambda: manager)
    app = FastAPI()
    app.include_router(experiments.router)
    try:
        with TestClient(app) as client:
            assert client.post("/api/experiments/missing/run", json={"confirm_python_execution": True}).status_code == 404
            for _ in range(2):
                response = client.post(f"/api/experiments/{experiment['id']}/run", json={"confirm_python_execution": True})
                assert response.status_code == 202
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = repository.get_experiment(experiment["id"])
            if state["execution"]["status"] != "running":
                break
            time.sleep(0.02)
        assert state["execution"]["status"] == "succeeded", state
        assert [trial["status"] for trial in state["trials"]] == ["failed", "succeeded", "succeeded"]
        assert state["execution"]["selection"]["candidate_id"] == "2"
        assert [call[0] for call in calls] == [spec["validation"]["start"], spec["validation"]["start"], spec["test"]["start"]]
        assert all(call[2] == experiment["protocol"]["settings"] and call[3] == 1 for call in calls)
    finally:
        manager.shutdown()
        jobs.shutdown()


def test_budget_blocks_new_jobs_and_preserves_completed_trials(setup_experiment):
    repository, spec = setup_experiment
    identity = repository.create_experiment(spec)["id"]
    protocol = repository.get_experiment(identity)["protocol"]
    protocol["max_trials"] = 1
    with repository._conn:
        repository._conn.execute(
            "UPDATE research_experiments SET protocol_json = ? WHERE id = ?", (json.dumps(protocol), identity)
        )
    repository.start_experiment(identity)
    complete_trial(repository, repository.advance_experiment(identity), 1.0)
    assert repository.advance_experiment(identity) is None
    state = repository.get_experiment(identity)
    assert state["execution"]["error"] == "trial budget exhausted"
    assert [trial["status"] for trial in state["trials"]] == ["succeeded", "planned"]
    assert repository._conn.execute("SELECT COUNT(*) FROM backtest_jobs").fetchone()[0] == 1


def test_start_requires_confirmation_and_rejects_unimplemented_training(setup_experiment, monkeypatch):
    repository, spec = setup_experiment
    spec["training"] = {"start": "2023-01-01", "end": "2023-12-31"}
    identity = repository.create_experiment(spec)["id"]
    with pytest.raises(ValueError, match="training"):
        repository.start_experiment(identity)
    assert repository.get_experiment(identity)["execution"] is None
    app = FastAPI()
    app.include_router(experiments.router)
    with TestClient(app) as client:
        assert client.post(f"/api/experiments/{identity}/run", json={}).status_code == 409
