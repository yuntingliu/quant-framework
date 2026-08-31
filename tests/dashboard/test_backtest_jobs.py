from __future__ import annotations

import time

from alphalab.store import ResultStore
from dashboard.backend.services.backtest_job_service import BacktestJobManager


def test_background_backtest_job_persists_success(tmp_path):
    def run(
        project_id: str, start_date: str, end_date: str, profile: str, revision: int | None
    ) -> dict:
        return {
            "id": "backtest-1",
            "project_id": project_id,
            "start_date": start_date,
            "end_date": end_date,
            "profile": profile,
            "revision": revision,
        }

    manager = BacktestJobManager(tmp_path / "jobs.db", runner=run)
    try:
        submitted = manager.submit(
            {
                "project_id": "sdk-v1-default",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "profile": "demo",
                "revision": 1,
            }
        )
        deadline = time.monotonic() + 2
        job = submitted
        while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
            job = manager.get(submitted["id"])
            assert job is not None
    finally:
        manager.shutdown()

    assert job["status"] == "succeeded"
    assert job["result_id"] == "backtest-1"
    assert job["result"]["project_id"] == "sdk-v1-default"


def test_interrupted_backtest_job_is_recovered_with_a_stable_result_id(tmp_path):
    database = tmp_path / "recovered-jobs.db"
    request = {
        "project_id": "sdk-v1-default",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "profile": "demo",
        "revision": 1,
    }
    store = ResultStore(database)
    try:
        job_id = store.create_backtest_job(request)
        store.update_backtest_job(job_id, status="running")
    finally:
        store.close()

    calls = []

    def run(
        project_id: str,
        start_date: str,
        end_date: str,
        profile: str,
        revision: int | None,
        *,
        backtest_id: str,
    ) -> dict:
        calls.append(backtest_id)
        return {"id": backtest_id, "project_id": project_id}

    manager = BacktestJobManager(database, runner=run)
    try:
        deadline = time.monotonic() + 2
        job = manager.get(job_id)
        while job and job["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
            job = manager.get(job_id)
    finally:
        manager.shutdown()

    assert job is not None
    assert job["status"] == "succeeded"
    assert job["result_id"] == job_id
    assert calls == [job_id]
