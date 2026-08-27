"""Persistent background jobs for long-running complete backtests."""

from __future__ import annotations

import inspect
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Callable, Iterator

import pandas as pd

from alphalab.store import ResultStore
from dashboard.backend.services import strategy_service, validation_service

BacktestRunner = Callable[..., dict]


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
        with self._store() as store:
            store.mark_backtest_jobs_interrupted()

    @contextmanager
    def _store(self) -> Iterator[ResultStore]:
        store = ResultStore(self._db_path)
        try:
            yield store
        finally:
            store.close()

    def submit(self, request: dict) -> dict:
        with self._store() as store:
            for existing in store.list_backtest_jobs(limit=100):
                if existing["status"] in {"queued", "running"} and existing["request"] == request:
                    return existing
            job_id = store.create_backtest_job(request)
            job = store.get_backtest_job(job_id)
        assert job is not None
        future = self._executor.submit(self._run, job_id, request)
        with self._lock:
            self._futures[job_id] = future
        future.add_done_callback(lambda _: self._forget(job_id))
        return job

    def get(self, job_id: str) -> dict | None:
        with self._store() as store:
            return store.get_backtest_job(job_id)

    def list(self, limit: int = 50) -> list[dict]:
        with self._store() as store:
            return store.list_backtest_jobs(limit=limit)

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)

    def _run(self, job_id: str, request: dict) -> None:
        with self._store() as store:
            store.update_backtest_job(
                job_id,
                status="running",
                message="Running complete strategy backtest",
            )
        try:
            arguments = (
                request["project_id"], request["start_date"], request["end_date"],
                request["profile"], request.get("revision"),
            )
            if "validation_revision" in inspect.signature(self._runner).parameters:
                result = self._runner(
                    *arguments, validation_revision=request.get("validation_revision")
                )
            else:
                result = self._runner(*arguments)
        except Exception as exc:
            with self._store() as store:
                store.update_backtest_job(
                    job_id,
                    status="failed",
                    message="Backtest failed",
                    error=str(exc) or type(exc).__name__,
                )
            return
        with self._store() as store:
            store.update_backtest_job(
                job_id,
                status="succeeded",
                message="Backtest completed",
                result=result,
                result_id=str(result["id"]),
            )


@lru_cache(maxsize=1)
def manager() -> BacktestJobManager:
    return BacktestJobManager()


def submit_backtest_job(request: dict) -> dict:
    profile = str(request.get("profile") or "")
    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
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
