"""RQ runtime synchronization plans, jobs, and execution."""
from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, field_validator

from alphalab.dataio.errors import DataLoadError, MissingDataError
from alphalab.dataio.fundamentals import (
    BALANCE_FIELDS,
    INCOME_FIELDS,
    build_canonical_fundamentals,
)
from alphalab.dataio.quality import validate_dataset
from alphalab.dataio.rq_sync import RQAcquirer
from alphalab.dataio.runtime import OperationsStore, RuntimeStore
from alphalab.dataio.symbols import canonical_a_share_symbol
from alphalab.utils.paths import DATA_DIR, RUNTIME_DIR

SyncDataset = Literal["instruments", "bars", "fundamentals"]
_ALLOWED_DATASETS = {"instruments", "bars", "fundamentals"}


class SyncRequest(BaseModel):
    source: Literal["rq"] = "rq"
    datasets: list[SyncDataset] = Field(
        default_factory=lambda: ["instruments", "bars", "fundamentals"]
    )
    symbols: list[str] | None = None
    start: str | None = None
    end: str | None = None
    force: bool = False

    @field_validator("datasets")
    @classmethod
    def validate_datasets(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(value).strip().lower() for value in values))
        unknown = sorted(set(normalized) - _ALLOWED_DATASETS)
        if unknown:
            raise ValueError(f"Unsupported datasets: {unknown}")
        if not normalized:
            raise ValueError("At least one dataset is required")
        return normalized

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = list(dict.fromkeys(canonical_a_share_symbol(value) for value in values))
        if not normalized:
            raise ValueError("symbols must not be empty")
        return normalized


class SyncCancelled(RuntimeError):
    pass


def build_sync_plan(
    request: SyncRequest,
    *,
    root: str | Path | None = None,
) -> dict:
    runtime_root = Path(root) if root is not None else RUNTIME_DIR
    store = RuntimeStore(runtime_root)
    end = pd.Timestamp(request.end or date.today()).normalize()
    start = pd.Timestamp(request.start or (end - pd.DateOffset(years=5))).normalize()
    if start > end:
        raise ValueError("start must be on or before end")
    symbols = request.symbols or _sample_symbols()
    if not symbols:
        raise MissingDataError("No symbols supplied and bundled manifest has no universe")

    bars_start = start
    bars_watermark = store.operations.watermark("rq.bars")
    if bars_watermark and not request.force:
        bars_start = max(start, pd.Timestamp(bars_watermark) - pd.Timedelta(days=7))

    start_quarter = _date_quarter(start)
    end_quarter = _date_quarter(end)
    finance_mode = "full"
    if (
        not request.force
        and store.catalog.status("canonical.fundamentals")["status"] == "ready"
    ):
        start_quarter = _shift_quarter(end_quarter, -7)
        finance_mode = "revision_lookback"

    steps: list[dict[str, Any]] = []
    if "instruments" in request.datasets:
        steps.append(
            {
                "dataset": "rq.instruments",
                "mode": "snapshot",
                "start": end.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
            }
        )
    if "bars" in request.datasets:
        steps.append(
            {
                "dataset": "rq.bars",
                "mode": "full" if request.force or not bars_watermark else "incremental",
                "start": bars_start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
                "watermark": bars_watermark,
            }
        )
    if "fundamentals" in request.datasets:
        steps.extend(
            [
                {
                    "dataset": "rq.financials.income",
                    "mode": finance_mode,
                    "start_quarter": start_quarter,
                    "end_quarter": end_quarter,
                },
                {
                    "dataset": "rq.financials.balance",
                    "mode": finance_mode,
                    "start_quarter": start_quarter,
                    "end_quarter": end_quarter,
                },
                {
                    "dataset": "canonical.fundamentals",
                    "mode": "rebuild",
                    "asof_date": end.strftime("%Y-%m-%d"),
                },
            ]
        )
    return {
        "source": "rq",
        "runtime_root": str(runtime_root),
        "symbols": symbols,
        "symbol_count": len(symbols),
        "requested_start": start.strftime("%Y-%m-%d"),
        "requested_end": end.strftime("%Y-%m-%d"),
        "force": request.force,
        "steps": steps,
        "estimated_batches": _estimate_batches(steps, len(symbols)),
        "writes_are_local": True,
    }


class RQSyncService:
    def __init__(
        self,
        root: str | Path | None = None,
        *,
        acquirer: RQAcquirer | None = None,
    ):
        self.root = Path(root) if root is not None else RUNTIME_DIR
        self.store = RuntimeStore(self.root)
        self.operations = self.store.operations
        self.acquirer = acquirer

    def run(self, job_id: str) -> dict:
        job = self.operations.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        request = SyncRequest.model_validate(job["request"])
        plan = build_sync_plan(request, root=self.root)
        acquirer = self.acquirer or RQAcquirer.from_env()
        total = len(plan["steps"])
        progress = 0
        self.operations.update_job(
            job_id,
            status="running",
            total=total,
            message="Connecting to RQData",
        )
        try:
            datasets = set(request.datasets)
            if "instruments" in datasets:
                self._check_cancel(job_id)
                frame = acquirer.instruments(plan["requested_end"])
                self.store.write("rq.instruments", frame)
                progress = self._complete_step(job_id, "rq.instruments", progress, total)

            if "bars" in datasets:
                self._check_cancel(job_id)
                step = _step(plan, "rq.bars")
                bars = acquirer.daily_bars(
                    plan["symbols"],
                    step["start"],
                    step["end"],
                    progress=lambda message: self.operations.update_job(
                        job_id,
                        message=message,
                    ),
                    cancelled=lambda: self.operations.is_cancel_requested(job_id),
                )
                self._check_cancel(job_id)
                self.store.write("rq.bars", bars)
                progress = self._complete_step(job_id, "rq.bars", progress, total)

            if "fundamentals" in datasets:
                income_step = _step(plan, "rq.financials.income")
                balance_step = _step(plan, "rq.financials.balance")
                self._check_cancel(job_id)
                income = acquirer.financials(
                    plan["symbols"],
                    list(INCOME_FIELDS),
                    income_step["start_quarter"],
                    income_step["end_quarter"],
                    progress=lambda message: self.operations.update_job(
                        job_id,
                        message=message,
                    ),
                    cancelled=lambda: self.operations.is_cancel_requested(job_id),
                )
                self._check_cancel(job_id)
                self.store.write("rq.financials.income", income)
                progress = self._complete_step(
                    job_id,
                    "rq.financials.income",
                    progress,
                    total,
                )

                balance = acquirer.financials(
                    plan["symbols"],
                    list(BALANCE_FIELDS),
                    balance_step["start_quarter"],
                    balance_step["end_quarter"],
                    progress=lambda message: self.operations.update_job(
                        job_id,
                        message=message,
                    ),
                    cancelled=lambda: self.operations.is_cancel_requested(job_id),
                )
                self._check_cancel(job_id)
                self.store.write("rq.financials.balance", balance)
                progress = self._complete_step(
                    job_id,
                    "rq.financials.balance",
                    progress,
                    total,
                )

                try:
                    all_bars = self.store.read("rq.bars")
                except MissingDataError as exc:
                    raise MissingDataError(
                        "Canonical fundamentals require runtime bars. Sync bars first."
                    ) from exc
                canonical = build_canonical_fundamentals(
                    self.store.read("rq.financials.income"),
                    self.store.read("rq.financials.balance"),
                    all_bars,
                    asof_date=plan["requested_end"],
                )
                self.store.write("canonical.fundamentals", canonical)
                progress = self._complete_step(
                    job_id,
                    "canonical.fundamentals",
                    progress,
                    total,
                )

            self.operations.update_job(
                job_id,
                status="succeeded",
                progress=total,
                total=total,
                message="RQ runtime data is ready",
            )
        except SyncCancelled:
            self.operations.update_job(
                job_id,
                status="cancelled",
                progress=progress,
                total=total,
                message="Cancelled at a batch boundary",
            )
        except Exception as exc:
            self.operations.update_job(
                job_id,
                status="failed",
                progress=progress,
                total=total,
                message="RQ sync failed",
                error=_public_error(exc),
            )
        result = self.operations.get_job(job_id)
        assert result is not None
        return result

    def _complete_step(
        self,
        job_id: str,
        dataset: str,
        progress: int,
        total: int,
    ) -> int:
        report = validate_dataset(dataset, self.root)
        if report["status"] != "passed":
            raise DataLoadError(f"Quality validation failed for {dataset}")
        current = progress + 1
        self.operations.save_checkpoint(job_id, dataset, "complete")
        self.operations.update_job(
            job_id,
            progress=current,
            total=total,
            message=f"Completed {dataset}",
        )
        return current

    def _check_cancel(self, job_id: str) -> None:
        if self.operations.is_cancel_requested(job_id):
            raise SyncCancelled()


class SyncJobManager:
    """One-process, single-worker queue backed by the runtime operations DB."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else RUNTIME_DIR
        self.operations = OperationsStore(self.root)
        self.operations.mark_interrupted()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rq-sync")
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, request: SyncRequest) -> dict:
        active = [
            item
            for item in self.operations.list_jobs(limit=100)
            if item["status"] in {"queued", "running"}
        ]
        if active:
            raise DataLoadError(f"An RQ sync job is already active: {active[0]['id']}")
        job_id = self.operations.create_job(request.model_dump(mode="json"))
        with self._lock:
            self._futures[job_id] = self._executor.submit(
                RQSyncService(self.root).run,
                job_id,
            )
        result = self.operations.get_job(job_id)
        assert result is not None
        return result

    def run_now(self, request: SyncRequest) -> dict:
        job_id = self.operations.create_job(request.model_dump(mode="json"))
        return RQSyncService(self.root).run(job_id)

    def cancel(self, job_id: str) -> dict | None:
        if not self.operations.request_cancel(job_id):
            return self.operations.get_job(job_id)
        with self._lock:
            future = self._futures.get(job_id)
            if future and future.cancel():
                self.operations.update_job(
                    job_id,
                    status="cancelled",
                    message="Cancelled before start",
                )
        return self.operations.get_job(job_id)


def _sample_symbols() -> list[str]:
    path = DATA_DIR / "manifest.json"
    if not path.exists():
        return []
    values = json.loads(path.read_text(encoding="utf-8")).get("symbols", [])
    return list(dict.fromkeys(canonical_a_share_symbol(value) for value in values))


def _date_quarter(value: pd.Timestamp) -> str:
    return f"{value.year}q{value.quarter}"


def _shift_quarter(value: str, offset: int) -> str:
    year = int(value[:4])
    quarter = int(value[-1])
    index = year * 4 + quarter - 1 + offset
    return f"{index // 4}q{index % 4 + 1}"


def _step(plan: dict, dataset: str) -> dict:
    return next(item for item in plan["steps"] if item["dataset"] == dataset)


def _estimate_batches(steps: list[dict], symbol_count: int) -> int:
    stock_batches = max(1, (symbol_count + 199) // 200)
    total = 0
    for step in steps:
        if step["dataset"] == "rq.bars":
            total += stock_batches * 2
        elif step["dataset"].startswith("rq.financials."):
            start_year, start_quarter = int(step["start_quarter"][:4]), int(step["start_quarter"][-1])
            end_year, end_quarter = int(step["end_quarter"][:4]), int(step["end_quarter"][-1])
            quarters = (end_year - start_year) * 4 + end_quarter - start_quarter + 1
            total += stock_batches * max(1, (quarters + 49) // 50)
        else:
            total += 1
    return total


def _public_error(exc: BaseException) -> str:
    if isinstance(exc, DataLoadError):
        return str(exc)
    return f"{type(exc).__name__}: {str(exc)}"[:2000]


__all__ = [
    "RQSyncService",
    "SyncJobManager",
    "SyncRequest",
    "build_sync_plan",
]
