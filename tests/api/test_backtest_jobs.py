from __future__ import annotations

import time
from threading import Event, Timer

import pandas as pd

from alphalab.dataio import DataLoadError
from alphalab.store import ResultStore
from apps.api.services import (
    backtest_job_service,
    result_service,
    strategy_service,
    validation_service,
)
from apps.api.services.backtest_job_service import BacktestJobManager


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
            "metrics": {"sharpe": 1.2},
            "research_valid": True,
            "counts": {"events": 2500},
            "returns": [{"date": "2024-01-01", "value": 0.1}] * 2500,
            "execution": {"events": [{"secret": "large"}] * 2500},
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
    assert list(job)[:5] == [
        "status",
        "backtest_id",
        "error_code",
        "error_summary",
        "project_id",
    ]
    assert job["result_id"] == "backtest-1"
    assert job["project_id"] == "sdk-v1-default"
    assert job["metrics"] == {"sharpe": 1.2}
    assert job["counts"] == {
        "return_rows": 2500,
        "weight_rows": 0,
        "executions": 0,
        "events": 2500,
    }
    assert job["warnings"] == []
    assert job["research_valid"] is True
    assert job["attempted_trade_count"] == 0
    assert job["successful_trade_count"] == 0
    assert job["execution_data_fill_count"] == 0
    assert job["synthetic_state_count"] == 0
    assert job["market_state_rejection_count"] == 0
    assert job["suspension_rejection_count"] == 0
    assert job["limit_up_rejection_count"] == 0
    assert job["limit_down_rejection_count"] == 0
    assert job["capacity_rejection_count"] == 0
    assert job["cash_rejection_count"] == 0
    assert "result" not in job
    assert "result_summary" not in job
    assert "returns" not in job
    assert "execution" not in job


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


def test_two_service_managers_do_not_execute_the_same_job_twice(tmp_path):
    database = tmp_path / "shared-service-jobs.db"
    started = Event()
    release = Event()
    calls = []

    def run(*_args, **_kwargs) -> dict:
        calls.append("run")
        started.set()
        release.wait(timeout=5)
        return {"id": "shared-result", "project_id": "sdk-v1-default"}

    first = BacktestJobManager(database, runner=run)
    second = None
    try:
        submitted = first.submit(
            {
                "project_id": "sdk-v1-default",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "profile": "runtime",
                "revision": 1,
            }
        )
        assert started.wait(timeout=1)
        second = BacktestJobManager(database, runner=run)
        time.sleep(0.05)
        assert calls == ["run"]
        release.set()
        deadline = time.monotonic() + 2
        job = first.get(submitted["id"])
        while job and job["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
            job = first.get(submitted["id"])
    finally:
        release.set()
        first.shutdown()
        if second is not None:
            second.shutdown()

    assert job is not None
    assert job["status"] == "succeeded"
    assert calls == ["run"]


def test_success_clears_stale_terminal_error_fields(tmp_path):
    database = tmp_path / "terminal-state.db"
    store = ResultStore(database)
    try:
        job_id = store.create_backtest_job({"project_id": "sdk-v1-default"})
        store.update_backtest_job(
            job_id,
            status="failed",
            error="old failure",
            error_code="DATASET_BUSY",
            error_summary="old failure",
            error_details={"dataset": "rq.bars"},
            log_reference=f"backtest:{job_id}",
        )
        store.update_backtest_job(job_id, status="running")
        store.update_backtest_job(
            job_id,
            status="succeeded",
            result={"id": job_id},
            result_id=job_id,
        )
        job = store.get_backtest_job(job_id)
    finally:
        store.close()

    assert job is not None
    assert job["status"] == "succeeded"
    assert job["error"] is None
    assert job["error_code"] is None
    assert job["error_summary"] is None
    assert job["error_details"] == {}
    assert job["log_reference"] is None


def test_dataset_lock_failure_has_a_retryable_error_code(tmp_path):
    def fail(*_args) -> dict:
        raise DataLoadError("Dataset is locked by another process: rq.bars")

    manager = BacktestJobManager(tmp_path / "busy-jobs.db", runner=fail)
    try:
        submitted = manager.submit(
            {
                "project_id": "sdk-v1-default",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "profile": "runtime",
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

    assert job["status"] == "failed"
    assert job["error_code"] == "DATASET_BUSY"
    assert job["error_summary"] == "Runtime data is being updated; retry the backtest shortly."


def test_failed_job_exposes_stable_safe_error_fields(tmp_path):
    def fail(*_args) -> dict:
        raise ValueError(
            "C:\\service\\releases\\private.py "
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
            "internal traceback"
        )

    manager = BacktestJobManager(tmp_path / "failed-jobs.db", runner=fail)
    try:
        submitted = manager.submit(
            {
                "project_id": "sdk-v1-default",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "profile": "runtime",
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

    assert job["status"] == "failed"
    assert job["error_code"] == "INVALID_BACKTEST_REQUEST"
    assert job["log_reference"] == f"backtest:{job['id']}"
    assert "private.py" not in job["error_summary"]
    assert "0123456789abcdef" not in job["error_summary"]
    assert "traceback" not in job["error_summary"]
    assert "error" not in job
    assert job["warnings"] == []
    assert job["research_valid"] is None
    assert job["attempted_trade_count"] == 0
    assert job["successful_trade_count"] == 0
    assert job["execution_data_fill_count"] == 0
    assert job["market_state_rejection_count"] == 0


def test_submission_returns_before_background_backtest_finishes(tmp_path):
    backtest_started = Event()
    release_backtest = Event()

    def run(
        project_id: str,
        start_date: str,
        end_date: str,
        profile: str,
        revision: int | None,
    ) -> dict:
        backtest_started.set()
        release_backtest.wait(timeout=5)
        return {"id": "async-result", "project_id": project_id}

    manager = BacktestJobManager(
        tmp_path / "async-submission.db",
        runner=run,
    )
    try:
        started_at = time.monotonic()
        submitted = manager.submit(
            {
                "project_id": "sdk-v1-default",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "profile": "runtime",
                "revision": 1,
            }
        )
        elapsed = time.monotonic() - started_at
        assert elapsed < 1
        assert submitted["status"] == "queued"
        assert backtest_started.wait(timeout=1)
        running = manager.get(submitted["id"])
        assert running is not None
        assert running["status"] == "running"
        assert running["message"] == "Running complete strategy backtest"

        release_backtest.set()
        deadline = time.monotonic() + 2
        job = manager.get(submitted["id"])
        while job and job["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
            job = manager.get(submitted["id"])
    finally:
        release_backtest.set()
        manager.shutdown()

    assert job is not None
    assert job["status"] == "succeeded"


def test_wait_returns_when_a_running_job_reaches_terminal_state(tmp_path):
    backtest_started = Event()
    release_backtest = Event()

    def run(*_args) -> dict:
        backtest_started.set()
        release_backtest.wait(timeout=5)
        return {"id": "wait-result", "project_id": "sdk-v1-default"}

    manager = BacktestJobManager(tmp_path / "wait-jobs.db", runner=run)
    timer = None
    try:
        submitted = manager.submit(
            {
                "project_id": "sdk-v1-default",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "profile": "runtime",
                "revision": 1,
            }
        )
        assert backtest_started.wait(timeout=1)
        timer = Timer(0.05, release_backtest.set)
        timer.start()
        started_at = time.monotonic()
        job = manager.wait(submitted["id"], timeout_seconds=1)
        elapsed = time.monotonic() - started_at
    finally:
        release_backtest.set()
        if timer is not None:
            timer.join(timeout=1)
        manager.shutdown()

    assert job is not None
    assert job["status"] == "succeeded"
    assert job["backtest_id"] == "wait-result"
    assert elapsed < 1


def test_submission_path_queues_the_complete_backtest_directly(monkeypatch):
    submitted_requests = []

    class StubManager:
        def submit(self, request: dict) -> dict:
            submitted_requests.append(request)
            return {"status": "queued", "id": "direct-job"}

    monkeypatch.setattr(
        strategy_service,
        "get_project",
        lambda _project_id: {"current_revision": 3},
    )
    monkeypatch.setattr(
        strategy_service,
        "get_revision",
        lambda _project_id, _revision: {"revision": 3},
    )
    monkeypatch.setattr(
        validation_service,
        "get_workspace",
        lambda _project_id: {"current_revision": 2},
    )
    monkeypatch.setattr(backtest_job_service, "manager", lambda: StubManager())

    result = backtest_job_service.submit_backtest_job(
        {
            "project_id": "new-project",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "profile": "runtime",
        }
    )

    assert result == {"status": "queued", "id": "direct-job"}
    assert submitted_requests == [
        {
            "project_id": "new-project",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "profile": "runtime",
            "revision": 3,
            "validation_revision": 2,
        }
    ]


def test_backtest_summary_is_bounded_and_events_are_paged(tmp_path, monkeypatch):
    database = tmp_path / "paged-results.db"
    dates = pd.bdate_range("2024-01-01", periods=20)
    store = ResultStore(database)
    try:
        store.save_backtest(
            pd.Series(0.001, index=dates),
            {"sharpe": 1.0, "n_periods": len(dates)},
            backtest_id="paged-result",
            executions=[
                {
                    "entry_date": str(date)[:10],
                    "executed_weights": {"A": 0.5},
                    "source_sha256": "a" * 64,
                    "traceback": "private stack",
                }
                for date in dates
            ],
            events=[
                {"date": str(date)[:10], "kind": "daily", "state_sha256": "b" * 64}
                for date in dates
            ],
            validation_output={"warnings": ["validation warning"]},
            run_diagnostics={
                "warnings": ["execution warning"],
                "research_valid": False,
                "execution_summary": {
                    "attempted_trade_count": 14,
                    "successful_trade_count": 9,
                    "execution_data_fill_count": 27,
                    "synthetic_state_count": 27,
                    "market_state_rejection_count": 5,
                    "suspension_rejection_count": 2,
                    "limit_up_rejection_count": 1,
                    "limit_down_rejection_count": 3,
                    "capacity_rejection_count": 4,
                    "cash_rejection_count": 2,
                },
                "research_invalid_reasons": ["MISSING_EXECUTION_STATE"],
                "execution_fidelity": {"mean": 0.75},
            },
        )
    finally:
        store.close()
    monkeypatch.setattr(result_service, "ResultStore", lambda: ResultStore(database))

    summary = result_service.get_backtest_summary("paged-result")
    assert summary is not None
    assert list(summary)[:4] == [
        "status",
        "backtest_id",
        "error_code",
        "error_summary",
    ]
    assert summary["counts"] == {
        "return_rows": 20,
        "weight_rows": 0,
        "executions": 20,
        "events": 20,
    }
    assert len(summary["samples"]["returns"]["head"]) == 3
    assert len(summary["samples"]["returns"]["tail"]) == 3
    assert summary["warnings"] == ["execution warning", "validation warning"]
    assert summary["research_valid"] is False
    assert summary["attempted_trade_count"] == 14
    assert summary["successful_trade_count"] == 9
    assert summary["execution_data_fill_count"] == 27
    assert summary["synthetic_state_count"] == 27
    assert summary["market_state_rejection_count"] == 5
    assert summary["suspension_rejection_count"] == 2
    assert summary["limit_up_rejection_count"] == 1
    assert summary["limit_down_rejection_count"] == 3
    assert summary["capacity_rejection_count"] == 4
    assert summary["cash_rejection_count"] == 2
    assert summary["research_invalid_reasons"] == ["MISSING_EXECUTION_STATE"]
    assert summary["execution_fidelity"] == {"mean": 0.75}
    assert "returns" not in summary
    assert "weights" not in summary

    page = result_service.get_backtest_event_page(
        "paged-result", kind="executions", offset=5, limit=2
    )
    assert page is not None
    assert page["total"] == 20
    assert page["next_offset"] == 7
    assert len(page["rows"]) == 2
    assert "source_sha256" not in page["rows"][0]
    assert "traceback" not in page["rows"][0]


def test_project_quality_and_execution_reliability_survive_summary_and_job_boundary():
    from apps.api.services.backtest_job_service import _public_job
    from apps.api.services.result_service import build_backtest_summary

    assessment = {
        "passed": True, "reasons": [], "warnings": ["LOW_EXECUTION_FIDELITY"],
        "thresholds": {"require_target_tracking": False},
    }
    record = {
        "id": "quality-run", "run_diagnostics": {
            "execution_reliable": True, "execution_invalid_reasons": [],
            "execution_summary": {"attempted_trade_count": 10, "successful_trade_count": 6},
        },
        "validation_output": {"research_quality": assessment},
    }
    summary = build_backtest_summary(record)
    job = _public_job({"id": "quality-job", "status": "succeeded", "result": summary})
    for key in ("research_valid", "research_assessment", "execution_reliable", "execution_invalid_reasons", "warnings"):
        assert job[key] == summary[key]
    assert summary["research_valid"] is True
    assert summary["warnings"] == ["LOW_EXECUTION_FIDELITY"]
    assert summary["research_assessment"] == assessment
    record["validation_output"] = {}
    unassessed = build_backtest_summary(record)
    assert unassessed["research_valid"] is None
    assert unassessed["research_invalid_reasons"] == ["RESEARCH_QUALITY_NOT_EVALUATED"]
    assert unassessed["execution_reliable"] is True
