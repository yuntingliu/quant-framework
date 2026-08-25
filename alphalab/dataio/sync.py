"""RQ runtime synchronization plans, jobs, and execution."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

from alphalab.dataio.errors import DataLoadError, MissingDataError
from alphalab.dataio.factor_returns import build_factor_returns
from alphalab.dataio.fundamentals import (
    BALANCE_FIELDS,
    INCOME_FIELDS,
    build_canonical_fundamentals,
)
from alphalab.dataio.quality import validate_dataset
from alphalab.dataio.recipes import (
    DataRecipeError,
    execute_data_recipe,
    inspect_data_recipe_source,
)
from alphalab.dataio.rq_sync import RQAcquirer
from alphalab.dataio.rq_templates import get_rq_sync_template
from alphalab.dataio.runtime import OperationsStore, RuntimeStore
from alphalab.dataio.symbols import canonical_a_share_symbol
from alphalab.utils.paths import RUNTIME_DIR

SyncDataset = Literal["instruments", "bars", "fundamentals", "factors"]
_ALLOWED_DATASETS = {"instruments", "bars", "fundamentals", "factors"}


class SyncRequest(BaseModel):
    source: Literal["rq"] = "rq"
    template_id: str = "rq.a_share_research"
    datasets: list[SyncDataset] = Field(
        default_factory=lambda: ["instruments", "bars", "fundamentals", "factors"]
    )
    symbols: list[str] | None = None
    start: str | None = None
    end: str | None = None
    force: bool = False

    @model_validator(mode="before")
    @classmethod
    def resolve_template_defaults(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        template = get_rq_sync_template(normalized.get("template_id", "rq.a_share_research"))
        normalized["template_id"] = template.id
        normalized["datasets"] = list(template.resolve_datasets(normalized.get("datasets")))
        return normalized

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
    resolved_symbols: list[str] | None = None,
    symbol_source: str | None = None,
) -> dict:
    template = get_rq_sync_template(request.template_id)
    runtime_root = Path(root) if root is not None else RUNTIME_DIR
    store = RuntimeStore(runtime_root)
    end = pd.Timestamp(request.end or date.today()).normalize()
    start = pd.Timestamp(request.start or (end - pd.DateOffset(years=5))).normalize()
    if start > end:
        raise ValueError("start must be on or before end")
    cached_symbols = (
        _symbols_from_instruments(
            _read_cached_instruments(store),
            start=start,
            end=end,
            instrument_types=template.instrument_types,
        )
        if request.symbols is None and resolved_symbols is None
        else []
    )
    symbols = list(request.symbols or resolved_symbols or cached_symbols)
    symbols_resolved = bool(symbols)
    resolved_source = (
        "explicit_symbols"
        if request.symbols is not None
        else symbol_source
        or ("cached_rq_instruments" if cached_symbols else f"rq_template:{template.id}")
    )

    bars_start = start
    bars_watermark = store.operations.watermark("rq.bars")
    if bars_watermark and not request.force:
        bars_start = max(start, pd.Timestamp(bars_watermark) - pd.Timedelta(days=7))

    start_quarter = _date_quarter(start)
    end_quarter = _date_quarter(end)
    finance_mode = "full"
    if not request.force and store.catalog.status("canonical.fundamentals")["status"] == "ready":
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
    if "factors" in request.datasets:
        steps.append(
            {
                "dataset": "runtime.factor_returns",
                "mode": "rebuild",
                "start": start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
                "risk_free_source": "rq_yield_curve_1m",
            }
        )
    return {
        "source": "rq",
        "template_id": template.id,
        "template": template.to_dict(),
        "runtime_root": str(runtime_root),
        "scope": "custom" if request.symbols is not None else template.scope,
        "symbol_source": resolved_source,
        "symbols_resolved": symbols_resolved,
        "symbols": symbols,
        "symbol_count": len(symbols) if symbols_resolved else None,
        "requested_start": start.strftime("%Y-%m-%d"),
        "requested_end": end.strftime("%Y-%m-%d"),
        "force": request.force,
        "steps": steps,
        "estimated_batches": (_estimate_batches(steps, len(symbols)) if symbols_resolved else None),
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
        template = get_rq_sync_template(request.template_id)
        progress = 0
        total = 0
        self.operations.update_job(
            job_id,
            status="running",
            total=0,
            message=(
                f"Resolving {template.label} instruments from RQData"
                if request.symbols is None
                else "Preparing explicit RQData symbols"
            ),
        )
        instrument_frame: pd.DataFrame | None = None
        try:
            acquirer = self.acquirer or RQAcquirer.from_env()
            if request.symbols is None:
                unresolved = build_sync_plan(request, root=self.root)
                instrument_frame = acquirer.instruments(
                    unresolved["requested_end"],
                    instrument_types=template.instrument_types,
                    market=template.market,
                )
                symbols = _symbols_from_instruments(
                    instrument_frame,
                    start=pd.Timestamp(unresolved["requested_start"]),
                    end=pd.Timestamp(unresolved["requested_end"]),
                    instrument_types=template.instrument_types,
                )
                if not symbols:
                    raise MissingDataError(
                        f"RQData returned no {template.label} instruments active in "
                        "the requested range"
                    )
                plan = build_sync_plan(
                    request,
                    root=self.root,
                    resolved_symbols=symbols,
                    symbol_source="live_rq_instruments",
                )
            else:
                plan = build_sync_plan(request, root=self.root)
            total = len(plan["steps"])
            self.operations.update_job(
                job_id,
                total=total,
                message=f"RQData scope resolved: {plan['symbol_count']} symbols",
            )
        except Exception as exc:
            self.operations.update_job(
                job_id,
                status="failed",
                progress=progress,
                total=total,
                message="RQ sync failed while resolving the research scope",
                error=_public_error(exc),
            )
            result = self.operations.get_job(job_id)
            assert result is not None
            return result
        try:
            datasets = set(request.datasets)
            if "instruments" in datasets:
                self._check_cancel(job_id)
                frame = instrument_frame
                if frame is None:
                    frame = acquirer.instruments(
                        plan["requested_end"],
                        instrument_types=template.instrument_types,
                        market=template.market,
                    )
                frame = _select_instruments(frame, plan["symbols"])
                self.store.write("rq.instruments", frame)
                progress = self._complete_step(job_id, "rq.instruments", progress, total)

            if "bars" in datasets:
                self._check_cancel(job_id)
                step = _step(plan, "rq.bars")
                bars = acquirer.daily_bars(
                    plan["symbols"],
                    step["start"],
                    step["end"],
                    market=template.market,
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
                    market=template.market,
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
                    market=template.market,
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

            if "factors" in datasets:
                self._check_cancel(job_id)
                factor_step = _step(plan, "runtime.factor_returns")
                try:
                    factor_bars = self.store.read("rq.bars")
                    factor_fundamentals = self.store.read("canonical.fundamentals")
                except MissingDataError as exc:
                    raise MissingDataError(
                        "Runtime factors require bars and canonical fundamentals"
                    ) from exc
                risk_free = acquirer.risk_free_curve(
                    factor_step["start"],
                    factor_step["end"],
                )
                factors = build_factor_returns(
                    factor_bars,
                    factor_fundamentals,
                    risk_free,
                )
                if factors.empty:
                    raise MissingDataError(
                        "Runtime factor construction produced no monthly observations"
                    )
                self.store.write("runtime.factor_returns", factors)
                progress = self._complete_step(
                    job_id,
                    "runtime.factor_returns",
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
        self._ensure_idle()
        job_id = self.operations.create_job(request.model_dump(mode="json"))
        with self._lock:
            self._futures[job_id] = self._executor.submit(
                RQSyncService(self.root).run,
                job_id,
            )
        result = self.operations.get_job(job_id)
        assert result is not None
        return result

    def submit_recipe(self, source: str, *, project_id: str) -> dict:
        self._ensure_idle()
        inspection = inspect_data_recipe_source(source)
        job_id = self.operations.create_job(
            {
                "source": "rq",
                "kind": "python_recipe",
                "project_id": project_id,
                "recipe_source": source,
                "recipe_source_sha256": inspection.source_sha256,
            }
        )
        with self._lock:
            self._futures[job_id] = self._executor.submit(
                _run_recipe_job,
                self.root,
                job_id,
            )
        result = self.operations.get_job(job_id)
        assert result is not None
        return result

    def run_now(self, request: SyncRequest) -> dict:
        job_id = self.operations.create_job(request.model_dump(mode="json"))
        return RQSyncService(self.root).run(job_id)

    def run_recipe_now(self, source: str, *, project_id: str = "test") -> dict:
        inspection = inspect_data_recipe_source(source)
        job_id = self.operations.create_job(
            {
                "source": "rq",
                "kind": "python_recipe",
                "project_id": project_id,
                "recipe_source": source,
                "recipe_source_sha256": inspection.source_sha256,
            }
        )
        return _run_recipe_job(self.root, job_id)

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

    def _ensure_idle(self) -> None:
        active = [
            item
            for item in self.operations.list_jobs(limit=100)
            if item["status"] in {"queued", "running"}
        ]
        if active:
            raise DataLoadError(f"An RQ sync job is already active: {active[0]['id']}")


def _run_recipe_job(root: Path, job_id: str) -> dict:
    operations = OperationsStore(root)
    job = operations.get_job(job_id)
    if job is None:
        raise KeyError(job_id)
    request = dict(job["request"])
    source = str(request.get("recipe_source") or "")
    operations.update_job(
        job_id,
        status="running",
        total=1,
        message="Executing the saved Python data recipe",
    )
    try:
        execution = execute_data_recipe(
            source,
            mode="run",
            root=root,
            cancelled=lambda: operations.is_cancel_requested(job_id),
        )
        audit = {
            "recipe_source_sha256": execution["source_sha256"],
            "recipe_stdout": execution["stdout"],
            "recipe_stderr": execution["stderr"],
            "recipe_planned": execution["planned"],
            "recipe_published": execution["published"],
            "recipe_outputs": execution["outputs"],
        }
        sync_request = execution.get("sync_request")
        if sync_request is not None:
            resolved = SyncRequest.model_validate(sync_request).model_dump(mode="json")
            operations.replace_job_request(job_id, {**request, **resolved, **audit})
            if operations.is_cancel_requested(job_id):
                operations.update_job(
                    job_id,
                    status="cancelled",
                    message="Python data recipe cancelled before synchronization",
                )
                result = operations.get_job(job_id)
                assert result is not None
                return result
            return RQSyncService(root).run(job_id)
        operations.replace_job_request(job_id, {**request, **audit})
        completed = max(1, len(execution["published"]) + len(execution["outputs"]))
        operations.update_job(
            job_id,
            status="succeeded",
            progress=completed,
            total=completed,
            message="Python data recipe completed",
        )
    except Exception as exc:
        details = exc.traceback_text if isinstance(exc, DataRecipeError) else None
        if operations.is_cancel_requested(job_id):
            operations.update_job(
                job_id,
                status="cancelled",
                message="Python data recipe cancelled",
            )
        else:
            operations.update_job(
                job_id,
                status="failed",
                message="Python data recipe failed",
                error=(details or _public_error(exc))[:20_000],
            )
    result = operations.get_job(job_id)
    assert result is not None
    return result


def _read_cached_instruments(store: RuntimeStore) -> pd.DataFrame:
    try:
        return store.read("rq.instruments")
    except MissingDataError:
        return pd.DataFrame()


def _symbols_from_instruments(
    frame: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    instrument_types: tuple[str, ...] | None = None,
) -> list[str]:
    if frame.empty or "symbol" not in frame:
        return []
    candidates = frame.copy()
    if instrument_types:
        requested_types = {value.upper() for value in instrument_types}
        if "asset_type" in candidates:
            candidates = candidates.loc[
                candidates["asset_type"].astype(str).str.upper().isin(requested_types)
            ]
        elif requested_types != {"CS"}:
            return []
    if "snapshot_date" in candidates:
        candidates["snapshot_date"] = pd.to_datetime(candidates["snapshot_date"], errors="coerce")
        snapshots = candidates["snapshot_date"].dropna()
        if not snapshots.empty:
            on_or_before = snapshots.loc[snapshots.le(end)]
            chosen = on_or_before.max() if not on_or_before.empty else snapshots.min()
            candidates = candidates.loc[candidates["snapshot_date"].eq(chosen)]
    listed = pd.to_datetime(
        candidates.get("listed_date", pd.Series(pd.NaT, index=candidates.index)),
        errors="coerce",
    )
    delisted = pd.to_datetime(
        candidates.get("de_listed_date", pd.Series(pd.NaT, index=candidates.index)),
        errors="coerce",
    )
    candidates = candidates.loc[
        (listed.isna() | listed.le(end)) & (delisted.isna() | delisted.ge(start))
    ]
    return sorted(
        dict.fromkeys(
            canonical_a_share_symbol(value) for value in candidates["symbol"].dropna().astype(str)
        )
    )


def _select_instruments(frame: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    requested = set(symbols)
    selected = frame.loc[frame["symbol"].astype(str).isin(requested)].copy()
    available = set(selected["symbol"].astype(str))
    missing = sorted(requested - available)
    if missing:
        raise MissingDataError(
            f"RQData instrument snapshot is missing requested symbols: {missing[:10]}"
        )
    return selected


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
            start_year, start_quarter = int(step["start_quarter"][:4]), int(
                step["start_quarter"][-1]
            )
            end_year, end_quarter = int(step["end_quarter"][:4]), int(step["end_quarter"][-1])
            quarters = (end_year - start_year) * 4 + end_quarter - start_quarter + 1
            total += stock_batches * max(1, (quarters + 49) // 50)
        elif step["dataset"] == "runtime.factor_returns":
            total += 2
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
