from __future__ import annotations

import time

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
