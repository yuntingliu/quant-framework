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
from alphalab.dataio.rq_templates import DEFAULT_RQ_SYNC_TEMPLATE_ID, get_rq_sync_template
from alphalab.dataio.runtime import OperationsStore, RuntimeStore
from alphalab.dataio.symbols import canonical_a_share_symbol, is_a_share_symbol
from alphalab.utils.paths import RUNTIME_DIR

SyncDataset = Literal[
    "instruments",
    "bars",
    "market-state",
    "daily-factors",
    "index-components",
    "fundamentals",
    "factors",
]
_ALLOWED_DATASETS = {
    "instruments",
    "bars",
    "market-state",
    "daily-factors",
    "index-components",
    "fundamentals",
    "factors",
}
_DEFAULT_INDEXES = ("000300.SH", "000905.SH", "000852.SH")


class SyncRequest(BaseModel):
    source: Literal["rq"] = "rq"
    template_id: str = DEFAULT_RQ_SYNC_TEMPLATE_ID
    datasets: list[SyncDataset] = Field(
        default_factory=lambda: ["instruments", "bars", "fundamentals", "factors"]
    )
    symbols: list[str] | None = None
    start: str | None = None
    end: str | None = None
    force: bool = False
    bar_chunk_days: int = Field(default=366, ge=1, le=3660)
    market_state_chunk_days: int = Field(default=366, ge=1, le=3660)
    daily_factors: list[str] = Field(default_factory=lambda: ["market_cap", "roe"])
    index_symbols: list[str] = Field(default_factory=lambda: list(_DEFAULT_INDEXES))
    component_frequency: Literal["ME", "W-FRI", "B"] = "ME"

    @model_validator(mode="before")
    @classmethod
    def resolve_template_defaults(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        template = get_rq_sync_template(normalized.get("template_id", DEFAULT_RQ_SYNC_TEMPLATE_ID))
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

    @field_validator("daily_factors")
    @classmethod
    def validate_daily_factors(cls, values: list[str]) -> list[str]:
        normalized = list(
            dict.fromkeys(str(value).strip() for value in values if str(value).strip())
        )
        if not normalized:
            raise ValueError("daily_factors must not be empty")
        return normalized

    @field_validator("index_symbols")
    @classmethod
    def validate_index_symbols(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(canonical_a_share_symbol(value) for value in values))


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
                "date_chunk_days": request.bar_chunk_days,
            }
        )
    if "market-state" in request.datasets:
        state_datasets = ["rq.paused"]
        if "CS" in template.instrument_types:
            state_datasets.append("rq.is_st")
        for dataset in state_datasets:
            watermark = store.operations.watermark(dataset)
            state_start = start
            if watermark and not request.force:
                state_start = max(start, pd.Timestamp(watermark) - pd.Timedelta(days=1))
            steps.append(
                {
                    "dataset": dataset,
                    "mode": "full" if request.force or not watermark else "incremental",
                    "start": state_start.strftime("%Y-%m-%d"),
                    "end": end.strftime("%Y-%m-%d"),
                    "watermark": watermark,
                    "date_chunk_days": request.market_state_chunk_days,
                }
            )
    if "daily-factors" in request.datasets:
        field_watermarks = store.dimension_watermarks("rq.daily_factors", "field")
        field_starts = {
            field: (
                start
                if request.force or field not in field_watermarks
                else max(start, field_watermarks[field] - pd.Timedelta(days=1))
            ).strftime("%Y-%m-%d")
            for field in request.daily_factors
        }
        steps.append(
            {
                "dataset": "rq.daily_factors",
                "mode": (
                    "full"
                    if request.force or any(field not in field_watermarks for field in field_starts)
                    else "incremental"
                ),
                "start": min(field_starts.values()),
                "end": end.strftime("%Y-%m-%d"),
                "fields": request.daily_factors,
                "field_starts": field_starts,
                "date_chunk_days": request.market_state_chunk_days,
            }
        )
    if "index-components" in request.datasets and request.index_symbols:
        index_watermarks = store.dimension_watermarks("rq.index_components", "index_symbol")
        index_starts = {
            symbol: (
                start
                if request.force or symbol not in index_watermarks
                else max(start, index_watermarks[symbol])
            ).strftime("%Y-%m-%d")
            for symbol in request.index_symbols
        }
        steps.append(
            {
                "dataset": "rq.index_components",
                "mode": (
                    "full"
                    if request.force
                    or any(symbol not in index_watermarks for symbol in index_starts)
                    else "incremental"
                ),
                "start": min(index_starts.values()),
                "end": end.strftime("%Y-%m-%d"),
                "indexes": request.index_symbols,
                "index_starts": index_starts,
                "frequency": request.component_frequency,
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
        "force_semantics": "rewrite_requested_range_keep_rows_outside_range",
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
                wrote_bars = False
                for group_start, group_symbols in _symbol_sync_groups(
                    self.store,
                    ["rq.bars"],
                    plan["symbols"],
                    default_start=step["start"],
                    requested_start=plan["requested_start"],
                    instruments=(
                        instrument_frame
                        if instrument_frame is not None
                        else _read_cached_instruments(self.store)
                    ),
                    force=request.force,
                    overlap_days=7,
                ).items():
                    for bars in _daily_bar_chunks(
                        acquirer,
                        group_symbols,
                        group_start,
                        step["end"],
                        market=template.market,
                        date_chunk_days=request.bar_chunk_days,
                        progress=lambda message: self.operations.update_job(
                            job_id, message=message
                        ),
                        cancelled=lambda: self.operations.is_cancel_requested(job_id),
                    ):
                        self._check_cancel(job_id)
                        self.store.write("rq.bars", bars)
                        self.operations.save_checkpoint(
                            job_id,
                            "rq.bars",
                            pd.to_datetime(bars["date"]).max().strftime("%Y-%m-%d"),
                        )
                        wrote_bars = True
                self._check_cancel(job_id)
                if not wrote_bars:
                    raise MissingDataError("RQData returned an empty response for daily bars")
                progress = self._complete_step(job_id, "rq.bars", progress, total)

            if "market-state" in datasets:
                paused_step = _step(plan, "rq.paused")
                state_datasets = ["rq.paused"]
                include_st = any(item["dataset"] == "rq.is_st" for item in plan["steps"])
                if include_st:
                    state_datasets.append("rq.is_st")
                groups = _symbol_sync_groups(
                    self.store,
                    state_datasets,
                    plan["symbols"],
                    default_start=paused_step["start"],
                    requested_start=plan["requested_start"],
                    instruments=(
                        instrument_frame
                        if instrument_frame is not None
                        else _read_cached_instruments(self.store)
                    ),
                    force=request.force,
                    overlap_days=1,
                )
                wrote_state = False
                for group_start, group_symbols in groups.items():
                    for paused, is_st in acquirer.market_state_chunks(
                        group_symbols,
                        group_start,
                        paused_step["end"],
                        market=template.market,
                        include_st=include_st,
                        date_chunk_days=request.market_state_chunk_days,
                        progress=lambda message: self.operations.update_job(
                            job_id, message=message
                        ),
                        cancelled=lambda: self.operations.is_cancel_requested(job_id),
                    ):
                        self._check_cancel(job_id)
                        self.store.write("rq.paused", paused)
                        if include_st:
                            self.store.write("rq.is_st", is_st)
                        checkpoint = pd.to_datetime(paused["date"]).max().strftime("%Y-%m-%d")
                        self.operations.save_checkpoint(job_id, "rq.paused", checkpoint)
                        if include_st:
                            self.operations.save_checkpoint(job_id, "rq.is_st", checkpoint)
                        wrote_state = True
                self._check_cancel(job_id)
                if not wrote_state:
                    raise MissingDataError("RQData returned no historical market state")
                progress = self._complete_step(job_id, "rq.paused", progress, total)
                if include_st:
                    progress = self._complete_step(job_id, "rq.is_st", progress, total)

            if "daily-factors" in datasets:
                factor_step = _step(plan, "rq.daily_factors")
                wrote_daily_factors = False
                for field, field_start in factor_step["field_starts"].items():
                    groups = _symbol_sync_groups(
                        self.store,
                        ["rq.daily_factors"],
                        plan["symbols"],
                        default_start=field_start,
                        requested_start=plan["requested_start"],
                        instruments=(
                            instrument_frame
                            if instrument_frame is not None
                            else _read_cached_instruments(self.store)
                        ),
                        force=request.force,
                        overlap_days=1,
                        dimension=("field", field),
                    )
                    for group_start, group_symbols in groups.items():
                        for daily_factors in acquirer.daily_factor_chunks(
                            group_symbols,
                            [field],
                            group_start,
                            factor_step["end"],
                            market=template.market,
                            date_chunk_days=request.market_state_chunk_days,
                            progress=lambda message: self.operations.update_job(
                                job_id, message=message
                            ),
                            cancelled=lambda: self.operations.is_cancel_requested(job_id),
                        ):
                            self._check_cancel(job_id)
                            self.store.write("rq.daily_factors", daily_factors)
                            self.operations.save_checkpoint(
                                job_id,
                                "rq.daily_factors",
                                pd.to_datetime(daily_factors["date"]).max().strftime("%Y-%m-%d"),
                            )
                            wrote_daily_factors = True
                self._check_cancel(job_id)
                if not wrote_daily_factors:
                    raise MissingDataError("RQData returned no daily factors")
                progress = self._complete_step(job_id, "rq.daily_factors", progress, total)

            if "index-components" in datasets:
                component_step = _step(plan, "rq.index_components")
                wrote_components = False
                for component_start, indexes in _group_starts(
                    component_step["index_starts"]
                ).items():
                    dates = _component_dates(
                        component_start,
                        component_step["end"],
                        component_step["frequency"],
                    )
                    if not dates:
                        continue
                    components = acquirer.index_components(
                        indexes,
                        dates,
                        progress=lambda message: self.operations.update_job(
                            job_id, message=message
                        ),
                        cancelled=lambda: self.operations.is_cancel_requested(job_id),
                    )
                    self._check_cancel(job_id)
                    self.store.write("rq.index_components", components)
                    self.operations.save_checkpoint(
                        job_id,
                        "rq.index_components",
                        pd.to_datetime(components["date"]).max().strftime("%Y-%m-%d"),
                    )
                    wrote_components = True
                if not wrote_components:
                    raise MissingDataError("RQData returned no index components")
                progress = self._complete_step(job_id, "rq.index_components", progress, total)

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
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rq-sync")
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        for job in self.operations.recover_jobs():
            self._schedule(job["id"], job["request"])

    def submit(self, request: SyncRequest) -> dict:
        self._ensure_idle()
        payload = request.model_dump(mode="json")
        job_id = self.operations.create_job(payload)
        self._schedule(job_id, payload)
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
        self._schedule(job_id, {"kind": "python_recipe"})
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

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _ensure_idle(self) -> None:
        active = [
            item
            for item in self.operations.list_jobs(limit=100)
            if item["status"] in {"queued", "running"}
        ]
        if active:
            raise DataLoadError(f"An RQ sync job is already active: {active[0]['id']}")

    def _schedule(self, job_id: str, request: dict) -> None:
        target = (
            (_run_recipe_job, self.root, job_id)
            if request.get("kind") == "python_recipe"
            else (RQSyncService(self.root).run, job_id)
        )
        with self._lock:
            future = self._executor.submit(target[0], *target[1:])
            self._futures[job_id] = future
        future.add_done_callback(lambda _: self._forget(job_id))

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)


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
    if "asset_type" in candidates:
        is_common_stock = candidates["asset_type"].astype(str).str.upper().eq("CS")
        candidates = candidates.loc[~is_common_stock | candidates["symbol"].map(is_a_share_symbol)]
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


def _symbol_sync_groups(
    store: RuntimeStore,
    datasets: list[str],
    symbols: list[str],
    *,
    default_start: str,
    requested_start: str,
    instruments: pd.DataFrame,
    force: bool,
    overlap_days: int,
    dimension: tuple[str, str] | None = None,
) -> dict[str, list[str]]:
    """Group symbols by honest backfill start across all required datasets."""

    requested = pd.Timestamp(requested_start).normalize()
    default = pd.Timestamp(default_start).normalize()
    listing_starts: dict[str, pd.Timestamp] = {}
    if not instruments.empty and {"symbol", "listed_date"}.issubset(instruments.columns):
        listed = instruments[["symbol", "listed_date"]].copy()
        listed["symbol"] = listed["symbol"].astype(str).str.upper()
        listed["listed_date"] = pd.to_datetime(listed["listed_date"], errors="coerce")
        listing_starts = {
            str(symbol): pd.Timestamp(value).normalize()
            for symbol, value in listed.dropna().groupby("symbol")["listed_date"].min().items()
        }
    dataset_watermarks = [
        store.watermarks(
            dataset,
            dimension=dimension,
            required_columns=("raw_open", "raw_high", "raw_low", "raw_close")
            if dataset == "rq.bars"
            else (),
        )
        for dataset in datasets
    ]
    grouped: dict[str, list[str]] = {}
    for raw_symbol in symbols:
        symbol = str(raw_symbol).upper()
        base = max(requested, listing_starts.get(symbol, requested))
        effective = base
        if not force:
            values = [watermarks.get(symbol) for watermarks in dataset_watermarks]
            if values and all(value is not None for value in values):
                effective = max(
                    base,
                    min(pd.Timestamp(value) for value in values if value is not None)
                    - pd.Timedelta(days=overlap_days),
                    default,
                )
        grouped.setdefault(effective.strftime("%Y-%m-%d"), []).append(symbol)
    return {start: sorted(values) for start, values in sorted(grouped.items())}


def _daily_bar_chunks(
    acquirer: Any,
    symbols: list[str],
    start: str,
    end: str,
    **kwargs: Any,
):
    """Keep older injected acquirers compatible while the built-in streams."""

    if hasattr(acquirer, "daily_bar_chunks"):
        yield from acquirer.daily_bar_chunks(symbols, start, end, **kwargs)
        return
    fallback_kwargs = {key: value for key, value in kwargs.items() if key != "date_chunk_days"}
    frame = acquirer.daily_bars(symbols, start, end, **fallback_kwargs)
    if frame is not None and not frame.empty:
        yield frame


def _group_starts(values: dict[str, str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for dimension, start in values.items():
        grouped.setdefault(str(start), []).append(str(dimension))
    return {start: sorted(items) for start, items in sorted(grouped.items())}


def _component_dates(start: str, end: str, frequency: str) -> list[str]:
    first = pd.Timestamp(start).normalize()
    last = pd.Timestamp(end).normalize()
    if first > last:
        return []
    values = {first, last}
    values.update(pd.date_range(first, last, freq=frequency).normalize())
    return [value.strftime("%Y-%m-%d") for value in sorted(values)]


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
            chunks = max(
                1,
                (
                    (pd.Timestamp(step["end"]) - pd.Timestamp(step["start"])).days
                    // int(step.get("date_chunk_days", 366))
                )
                + 1,
            )
            total += stock_batches * chunks * 2
        elif step["dataset"] in {"rq.paused", "rq.is_st"}:
            chunks = max(
                1,
                (
                    (pd.Timestamp(step["end"]) - pd.Timestamp(step["start"])).days
                    // int(step.get("date_chunk_days", 366))
                )
                + 1,
            )
            total += stock_batches * chunks
        elif step["dataset"] == "rq.daily_factors":
            chunks = max(
                1,
                (
                    (pd.Timestamp(step["end"]) - pd.Timestamp(step["start"])).days
                    // int(step.get("date_chunk_days", 366))
                )
                + 1,
            )
            total += stock_batches * chunks * max(1, len(step.get("fields", [])))
        elif step["dataset"] == "rq.index_components":
            total += len(step.get("indexes", [])) * len(
                _component_dates(step["start"], step["end"], step.get("frequency", "ME"))
            )
        elif step["dataset"].startswith("rq.financials."):
            start_year, start_quarter = (
                int(step["start_quarter"][:4]),
                int(step["start_quarter"][-1]),
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
