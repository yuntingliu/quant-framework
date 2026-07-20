"""Partitioned parquet storage and local data-operation state."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import pandas as pd

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.errors import DataLoadError, MissingDataError
from alphalab.dataio.io_utils import atomic_write_parquet
from alphalab.utils.paths import RUNTIME_DIR

_OPERATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    watermark TEXT,
    rows INTEGER NOT NULL DEFAULT 0,
    files INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS partitions (
    dataset_id TEXT NOT NULL,
    path TEXT PRIMARY KEY,
    rows INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    min_date TEXT,
    max_date TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS sync_checkpoints (
    job_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    checkpoint TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, dataset_id)
);
CREATE TABLE IF NOT EXISTS quality_runs (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    status TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_error(exc: BaseException) -> str:
    text = str(exc)
    for marker in ("RQ_PASSWORD=", "password="):
        if marker in text:
            text = text.split(marker, 1)[0] + marker + "***"
    return text[:2000]


class OperationsStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else RUNTIME_DIR
        self.path = self.root / "app" / "dataio.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_OPERATIONS_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def mark_interrupted(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE sync_jobs
                   SET status='interrupted', finished_at=?, message='Service restarted'
                   WHERE status IN ('queued', 'running')""",
                (_now(),),
            )
            return int(cursor.rowcount)

    def create_job(self, request: dict[str, Any], *, source: str = "rq") -> str:
        job_id = uuid4().hex[:16]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO sync_jobs
                   (id, source, status, request_json, created_at)
                   VALUES (?, ?, 'queued', ?, ?)""",
                (job_id, source, json.dumps(request, sort_keys=True), _now()),
            )
        return job_id

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        total: int | None = None,
        message: str | None = None,
        error: str | None = None,
    ) -> None:
        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = status
            if status == "running":
                values["started_at"] = _now()
            if status in {"succeeded", "failed", "cancelled", "interrupted"}:
                values["finished_at"] = _now()
        if progress is not None:
            values["progress"] = progress
        if total is not None:
            values["total"] = total
        if message is not None:
            values["message"] = message
        if error is not None:
            values["error"] = error
        if not values:
            return
        assignments = ", ".join(f"{name}=?" for name in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE sync_jobs SET {assignments} WHERE id=?",
                (*values.values(), job_id),
            )

    def get_job(self, job_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM sync_jobs WHERE id=?", (job_id,)).fetchone()
        return self._job_dict(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sync_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._job_dict(row) for row in rows]

    @staticmethod
    def _job_dict(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["request"] = json.loads(item.pop("request_json"))
        item["cancel_requested"] = bool(item["cancel_requested"])
        return item

    def request_cancel(self, job_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE sync_jobs SET cancel_requested=1
                   WHERE id=? AND status IN ('queued', 'running')""",
                (job_id,),
            )
            return cursor.rowcount > 0

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM sync_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        return bool(row and row[0])

    def save_checkpoint(self, job_id: str, dataset: str, checkpoint: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO sync_checkpoints (job_id, dataset_id, checkpoint, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(job_id, dataset_id) DO UPDATE SET
                   checkpoint=excluded.checkpoint, updated_at=excluded.updated_at""",
                (job_id, dataset, checkpoint, _now()),
            )

    def record_partition(
        self,
        dataset: str,
        path: Path,
        rows: int,
        *,
        min_date: str | None,
        max_date: str | None,
    ) -> None:
        relative = path.relative_to(self.root).as_posix()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO partitions
                   (dataset_id, path, rows, sha256, min_date, max_date, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                   rows=excluded.rows, sha256=excluded.sha256,
                   min_date=excluded.min_date, max_date=excluded.max_date,
                   updated_at=excluded.updated_at""",
                (dataset, relative, rows, _sha256(path), min_date, max_date, _now()),
            )
            summary = DataCatalog(self.root).status(dataset)
            connection.execute(
                """INSERT INTO datasets (id, status, watermark, rows, files, updated_at, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                   watermark=excluded.watermark, rows=excluded.rows, files=excluded.files,
                   updated_at=excluded.updated_at, error=excluded.error""",
                (
                    dataset,
                    summary["status"],
                    summary.get("date_end"),
                    summary["rows"],
                    summary["files"],
                    _now(),
                    summary.get("error"),
                ),
            )

    def watermark(self, dataset: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT watermark FROM datasets WHERE id=?",
                (dataset,),
            ).fetchone()
        return str(row[0]) if row and row[0] else None

    def record_quality(self, dataset: str, report: dict) -> str:
        run_id = uuid4().hex[:16]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO quality_runs
                   (id, dataset_id, status, report_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, dataset, report["status"], json.dumps(report), _now()),
            )
        return run_id


class RuntimeStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else RUNTIME_DIR
        self.catalog = DataCatalog(self.root)
        self.operations = OperationsStore(self.root)

    @contextmanager
    def dataset_lock(self, dataset: str) -> Iterator[None]:
        key = f"{self.root.resolve()}::{dataset}"
        with _PROCESS_LOCKS_GUARD:
            process_lock = _PROCESS_LOCKS.setdefault(key, threading.Lock())
        if not process_lock.acquire(blocking=False):
            raise DataLoadError(f"Dataset is already being written: {dataset}")
        lock_path = self.root / ".locks" / f"{dataset.replace('.', '_')}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor: int | None = None
        try:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(descriptor, str(os.getpid()).encode("ascii"))
            except FileExistsError as exc:
                raise DataLoadError(f"Dataset is locked by another process: {dataset}") from exc
            yield
        finally:
            if descriptor is not None:
                os.close(descriptor)
                lock_path.unlink(missing_ok=True)
            process_lock.release()

    def read(self, dataset: str) -> pd.DataFrame:
        files = self.catalog.files(dataset)
        if not files:
            raise MissingDataError(f"Runtime dataset is missing: {dataset}")
        try:
            return pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
        except Exception as exc:
            raise DataLoadError(f"Could not read runtime dataset {dataset}: {_safe_error(exc)}") from exc

    def write(self, dataset: str, frame: pd.DataFrame) -> dict:
        if frame is None or frame.empty:
            raise MissingDataError(f"Refusing to overwrite {dataset} with an empty response")
        spec = self.catalog.spec(dataset)
        written: list[str] = []
        with self.dataset_lock(dataset):
            for path, part in self._partitions(dataset, frame):
                merged = part
                if path.exists():
                    previous = pd.read_parquet(path)
                    merged = pd.concat([previous, part], ignore_index=True)
                keys = [key for key in spec.key_columns if key in merged]
                if keys:
                    merged = merged.drop_duplicates(keys, keep="last")
                    merged = merged.sort_values(keys)
                atomic_write_parquet(merged.reset_index(drop=True), path)
                min_date, max_date = self._date_range(merged, spec.date_column)
                self.operations.record_partition(
                    dataset,
                    path,
                    len(merged),
                    min_date=min_date,
                    max_date=max_date,
                )
                written.append(str(path))
        status = self.catalog.status(dataset)
        return {"dataset": dataset, "written": written, "status": status}

    def _partitions(self, dataset: str, frame: pd.DataFrame) -> list[tuple[Path, pd.DataFrame]]:
        value = frame.copy()
        base = self.catalog.path(dataset)
        if dataset == "rq.instruments":
            value["snapshot_date"] = pd.to_datetime(value["snapshot_date"]).dt.normalize()
            return [
                (
                    base / f"snapshot_date={date:%Y-%m-%d}" / "part.parquet",
                    part,
                )
                for date, part in value.groupby("snapshot_date", sort=True)
            ]
        if dataset == "rq.bars":
            value["date"] = pd.to_datetime(value["date"]).dt.normalize()
            return [
                (
                    base / f"year={int(year):04d}" / f"month={int(month):02d}" / "part.parquet",
                    part,
                )
                for (year, month), part in value.groupby(
                    [value["date"].dt.year, value["date"].dt.month],
                    sort=True,
                )
            ]
        if dataset.startswith("rq.financials."):
            value["quarter"] = value["quarter"].astype(str).str.lower()
            return [
                (
                    base / f"report_year={year}" / "part.parquet",
                    part,
                )
                for year, part in value.groupby(value["quarter"].str[:4], sort=True)
            ]
        if dataset == "canonical.fundamentals":
            value["available_date"] = pd.to_datetime(value["available_date"]).dt.normalize()
            return [
                (
                    base / f"year={int(year):04d}" / "part.parquet",
                    part,
                )
                for year, part in value.groupby(value["available_date"].dt.year, sort=True)
            ]
        raise KeyError(f"Dataset does not have a partition policy: {dataset}")

    @staticmethod
    def _date_range(frame: pd.DataFrame, column: str | None) -> tuple[str | None, str | None]:
        if not column or column not in frame:
            return None, None
        values = pd.to_datetime(frame[column], errors="coerce").dropna()
        if values.empty:
            return None, None
        return values.min().strftime("%Y-%m-%d"), values.max().strftime("%Y-%m-%d")


__all__ = ["OperationsStore", "RuntimeStore"]
