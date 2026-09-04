"""Persistent background jobs for long-running complete backtests."""

from __future__ import annotations

import inspect
import logging
import re
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Callable, Iterator

import pandas as pd

from alphalab.dataio import DataLoadError, MissingDataError
from alphalab.dataio.runtime import RuntimeStore
from alphalab.store import ResultStore
from alphalab.strategy.engine import BacktestPreflightError
from alphalab.strategy.sdk_runtime import SdkRuntimeError
from dashboard.backend.services import strategy_service, validation_service

BacktestRunner = Callable[..., dict]
logger = logging.getLogger(__name__)

_PREFLIGHT_DETAIL_FIELDS = {
    "requested_start",
    "requested_end",
    "first_session",
    "last_session",
    "session_count",
    "eligible_symbol_dates",
    "complete_symbol_dates",
    "coverage",
    "missing_fields",
    "candidate_symbol_count",
    "listed_date_available",
    "delisted_date_available",
}


def _safe_error_summary(error: Exception) -> str:
    if isinstance(error, DataLoadError) and _is_dataset_busy(error):
        return "Runtime data is being updated; retry the backtest shortly."
    summary = (str(error) or type(error).__name__).splitlines()[0].strip()
    summary = re.sub(r"[A-Za-z]:\\[^\s\"']+", "<internal-path>", summary)
    summary = re.sub(
        r"(?<![:/\w])/(?!/)(?:[^/\s]+/)+[^\s\"']+",
        "<internal-path>",
        summary,
    )
    summary = re.sub(r"\b[a-fA-F0-9]{40,64}\b", "<internal-id>", summary)
    return summary[:500] or "Backtest failed"


def _is_dataset_busy(error: Exception) -> bool:
    summary = str(error).lower()
    return "dataset is locked" in summary or "dataset is already being written" in summary


def _error_code(error: Exception) -> str:
    if isinstance(error, BacktestPreflightError):
        return error.code
    if isinstance(error, MissingDataError):
        return "INSUFFICIENT_MARKET_STATE"
    if isinstance(error, DataLoadError):
        return "DATASET_BUSY" if _is_dataset_busy(error) else "DATA_LOAD_FAILED"
    if isinstance(error, SdkRuntimeError):
        return "STRATEGY_EXECUTION_FAILED"
    if isinstance(error, ValueError):
        return "INVALID_BACKTEST_REQUEST"
    return "BACKTEST_FAILED"


def _safe_error_details(error: Exception) -> dict:
    source = error.details if isinstance(error, BacktestPreflightError) else {}
    result: dict = {}
    for key in _PREFLIGHT_DETAIL_FIELDS:
        value = source.get(key)
        if value is None or isinstance(value, (bool, int, float)):
            if key in source:
                result[key] = value
        elif isinstance(value, str):
            result[key] = _safe_error_summary(ValueError(value))
        elif isinstance(value, (list, tuple)):
            result[key] = [
                _safe_error_summary(ValueError(str(item))) for item in value[:20]
            ]
    return result


def _result_summary(result: dict) -> dict:
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    warnings = (
        result.get("warnings")
        if isinstance(result.get("warnings"), list)
        else execution.get("warnings")
        if isinstance(execution.get("warnings"), list)
        else []
    )
    return {
        key: result.get(key)
        for key in ("id", "project_id", "start_date", "end_date", "profile")
        if result.get(key) is not None
    } | {
        "metrics": dict(result.get("metrics") or {}),
        "counts": dict(result.get("counts") or {}),
        "warnings": [_safe_error_summary(ValueError(str(item))) for item in warnings[:10]],
        "warnings_truncated": bool(result.get("warnings_truncated")) or len(warnings) > 10,
    }


def _public_job(job: dict) -> dict:
    status = job.get("status")
    failed = status in {"failed", "interrupted"}
    raw_result = (
        job.get("result")
        if status == "succeeded" and isinstance(job.get("result"), dict)
        else None
    )
    result = _result_summary(raw_result) if raw_result is not None else None
    error_summary = (
        _safe_error_summary(ValueError(str(job["error_summary"])))
        if failed and job.get("error_summary")
        else None
    )
    if not error_summary and failed and job.get("error"):
        error_summary = _safe_error_summary(ValueError(str(job["error"])))
    return {
        "status": status,
        "id": job.get("id"),
        "result_id": job.get("result_id") if status == "succeeded" else None,
        "error_code": (
            job.get("error_code") or "BACKTEST_FAILED"
            if failed
            else None
        ),
        "error_summary": error_summary,
        "error_details": dict(job.get("error_details") or {}) if failed else {},
        "log_reference": job.get("log_reference") if failed else None,
        "message": job.get("message"),
        "request": job.get("request"),
        "result_summary": result,
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }


class BacktestJobManager:
    """Run one backtest at a time and persist status across HTTP requests."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        runner: BacktestRunner | None = None,
    ) -> None:
        self._db_path = db_path
        self._runner = runner or strategy_service.run_project_backtest
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="alphalab-backtest",
        )
        self._futures: dict[str, Future[None]] = {}
        self._lock = Lock()
        self._job_locks = (
            RuntimeStore(Path(db_path).parent / ".backtest-job-runtime")
            if db_path is not None
            else RuntimeStore()
        )
        with self._store() as store:
            recovered = store.list_pending_backtest_jobs()
        for job in recovered:
            self._schedule(job["id"], job["request"])

    @contextmanager
    def _store(self) -> Iterator[ResultStore]:
        store = ResultStore(self._db_path)
        try:
            yield store
        finally:
            store.close()

    def submit(self, request: dict) -> dict:
        existing_job: dict | None = None
        with self._store() as store:
            for existing in store.list_backtest_jobs(limit=100):
                if (
                    existing["status"] in {"queued", "running"}
                    and existing["request"] == request
                ):
                    existing_job = existing
                    break
            if existing_job is None:
                job_id = store.create_backtest_job(request)
                job = store.get_backtest_job(job_id)
            else:
                job_id = existing_job["id"]
                job = existing_job
        assert job is not None
        self._schedule(job_id, request)
        return _public_job(job)

    def _schedule(self, job_id: str, request: dict) -> None:
        with self._lock:
            if job_id in self._futures:
                return
            future = self._executor.submit(self._run, job_id, request)
            self._futures[job_id] = future
        future.add_done_callback(lambda _: self._forget(job_id))

    def get(self, job_id: str) -> dict | None:
        with self._store() as store:
            job = store.get_backtest_job(job_id)
        if job is not None and job["status"] in {"queued", "running"}:
            self._schedule(job["id"], job["request"])
        return _public_job(job) if job is not None else None

    def list(self, limit: int = 50) -> list[dict]:
        with self._store() as store:
            jobs = store.list_backtest_jobs(limit=limit)
        for job in jobs:
            if job["status"] in {"queued", "running"}:
                self._schedule(job["id"], job["request"])
        return [_public_job(job) for job in jobs]

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)

    def _run(self, job_id: str, request: dict) -> None:
        lock_context = self._job_locks.dataset_lock(f"backtest-job.{job_id}")
        try:
            lock_context.__enter__()
        except DataLoadError:
            return
        try:
            self._run_owned(job_id, request)
        finally:
            lock_context.__exit__(None, None, None)

    def _run_owned(self, job_id: str, request: dict) -> None:
        with self._store() as store:
            current = store.get_backtest_job(job_id)
        if current is None or current["status"] in {"succeeded", "failed", "interrupted"}:
            return
        with self._store() as store:
            store.update_backtest_job(
                job_id,
                status="running",
                message="Running complete strategy backtest",
            )
        try:
            arguments = (
                request["project_id"],
                request["start_date"],
                request["end_date"],
                request["profile"],
                request.get("revision"),
            )
            parameters = inspect.signature(self._runner).parameters
            keyword_arguments = {}
            if "validation_revision" in parameters:
                keyword_arguments["validation_revision"] = request.get(
                    "validation_revision"
                )
            if "backtest_id" in parameters:
                keyword_arguments["backtest_id"] = job_id
            if keyword_arguments:
                result = self._runner(
                    *arguments,
                    **keyword_arguments,
                )
            else:
                result = self._runner(*arguments)
        except Exception as exc:
            code = _error_code(exc)
            summary = _safe_error_summary(exc)
            details = _safe_error_details(exc)
            log_reference = f"backtest:{job_id}"
            logger.exception("Backtest job %s failed with %s", job_id, code)
            with self._store() as store:
                store.update_backtest_job(
                    job_id,
                    status="failed",
                    message="Backtest failed",
                    error=summary,
                    error_code=code,
                    error_summary=summary,
                    error_details=details,
                    log_reference=log_reference,
                )
            return
        with self._store() as store:
            store.update_backtest_job(
                job_id,
                status="succeeded",
                message="Backtest completed",
                result=_result_summary(result),
                result_id=str(result["id"]),
            )


@lru_cache(maxsize=1)
def manager() -> BacktestJobManager:
    return BacktestJobManager()


def submit_backtest_job(request: dict) -> dict:
    profile = str(request.get("profile") or "")
    if profile != "runtime":
        raise ValueError("new backtests use the runtime data profile")
    try:
        start = pd.Timestamp(str(request.get("start_date") or ""))
        end = pd.Timestamp(str(request.get("end_date") or ""))
    except ValueError as exc:
        raise ValueError("start_date and end_date must be valid dates") from exc
    if pd.isna(start) or pd.isna(end) or start >= end:
        raise ValueError("start_date must be before end_date")
    project_id = str(request.get("project_id") or "").strip()
    project = strategy_service.get_project(project_id) if project_id else None
    if project is None:
        raise KeyError(project_id)
    raw_revision = request.get("revision")
    revision = int(raw_revision) if raw_revision is not None else int(project["current_revision"])
    if strategy_service.get_revision(project_id, revision) is None:
        raise KeyError(f"{project_id}@{revision}")
    validation = validation_service.get_workspace(project_id)
    normalized = {
        "project_id": project_id,
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "profile": profile,
        "revision": revision,
        "validation_revision": int(validation["current_revision"]),
    }
    return manager().submit(normalized)


def get_backtest_job(job_id: str) -> dict | None:
    return manager().get(job_id)


def list_backtest_jobs(limit: int = 50) -> list[dict]:
    return manager().list(limit=max(1, min(limit, 100)))


__all__ = [
    "BacktestJobManager",
    "get_backtest_job",
    "list_backtest_jobs",
    "manager",
    "submit_backtest_job",
]
