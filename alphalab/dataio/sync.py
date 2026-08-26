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
from alphalab.dataio.factor_returns import build_factor_returns
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

SyncDataset = Literal["instruments", "bars", "market-state", "fundamentals", "factors"]
_ALLOWED_DATASETS = {"instruments", "bars", "market-state", "fundamentals", "factors"}
_DEFAULT_INDEXES = ("000300.SH", "000905.SH", "000852.SH")
_FULL_UNIVERSE_ESTIMATE = 5_300


class SyncRequest(BaseModel):
    source: Literal["rq"] = "rq"
    datasets: list[SyncDataset] = Field(
        default_factory=lambda: [
            "instruments",
            "bars",
            "market-state",
            "fundamentals",
            "factors",
        ]
    )
    symbols: list[str] | None = None
    universe: Literal["sample", "all"] = "sample"
    start: str | None = None
    end: str | None = None
    force: bool = False
    bar_chunk_days: int = Field(default=366, ge=1, le=3660)
    market_state_chunk_days: int = Field(default=366, ge=1, le=3660)
    daily_factors: list[str] = Field(default_factory=lambda: ["market_cap", "roe"])
    index_symbols: list[str] = Field(default_factory=lambda: list(_DEFAULT_INDEXES))
    component_frequency: Literal["ME", "W-FRI", "B"] = "ME"

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
        normalized = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
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
) -> dict:
    runtime_root = Path(root) if root is not None else RUNTIME_DIR
    store = RuntimeStore(runtime_root)
    end = pd.Timestamp(request.end or date.today()).normalize()
    start = pd.Timestamp(request.start or (end - pd.DateOffset(years=5))).normalize()
    if start > end:
        raise ValueError("start must be on or before end")
    symbol_source = "explicit" if request.symbols else request.universe
    if request.symbols:
        symbols = request.symbols
    elif request.universe == "all":
        symbols = _stored_universe_symbols(store, start=start, end=end)
        if not symbols:
            symbol_source = "rq_instruments_at_run"
    else:
        symbols = _sample_symbols()
    if request.universe == "sample" and not symbols:
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
    if "instruments" in request.datasets or (request.universe == "all" and not request.symbols):
        steps.append(
            {
                "dataset": "rq.instruments",
                "mode": (
                    "snapshot"
                    if "instruments" in request.datasets
                    else "universe_snapshot"
                ),
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
        for dataset in ("rq.paused", "rq.is_st", "rq.daily_factors"):
            watermark = store.operations.watermark(dataset)
            dimension_starts: dict[str, str] | None = None
            if dataset == "rq.daily_factors":
                field_watermarks = _dimension_watermarks(store, dataset, "field")
                dimension_starts = {
                    field: (
                        start
                        if request.force or field not in field_watermarks
                        else max(start, field_watermarks[field] - pd.Timedelta(days=1))
                    ).strftime("%Y-%m-%d")
                    for field in request.daily_factors
                }
                step_start = min(
                    (pd.Timestamp(value) for value in dimension_starts.values()),
                    default=start,
                )
            else:
                step_start = start
                if watermark and not request.force:
                    step_start = max(start, pd.Timestamp(watermark) - pd.Timedelta(days=1))
            step: dict[str, Any] = {
                "dataset": dataset,
                "mode": (
                    "full"
                    if request.force
                    or not watermark
                    or (dimension_starts and any(field not in field_watermarks for field in dimension_starts))
                    else "incremental"
                ),
                "start": step_start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
                "watermark": watermark,
                "date_chunk_days": request.market_state_chunk_days,
            }
            if dataset == "rq.daily_factors":
                step["fields"] = request.daily_factors
                step["field_starts"] = dimension_starts
            steps.append(step)
        if request.index_symbols:
            component_watermark = store.operations.watermark("rq.index_components")
            index_watermarks = _dimension_watermarks(
                store,
                "rq.index_components",
                "index_symbol",
            )
            index_starts = {
                index_symbol: (
                    start
                    if request.force or index_symbol not in index_watermarks
                    else max(start, index_watermarks[index_symbol])
                ).strftime("%Y-%m-%d")
                for index_symbol in request.index_symbols
            }
            component_start = min(
                (pd.Timestamp(value) for value in index_starts.values()),
                default=start,
            )
            steps.append(
                {
                    "dataset": "rq.index_components",
                    "mode": (
                        "full"
                        if request.force
                        or not component_watermark
                        or any(index_symbol not in index_watermarks for index_symbol in index_starts)
                        else "incremental"
                    ),
                    "start": component_start.strftime("%Y-%m-%d"),
                    "end": end.strftime("%Y-%m-%d"),
                    "watermark": component_watermark,
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
        "runtime_root": str(runtime_root),
        "symbols": symbols,
        "symbol_count": len(symbols) if symbols else None,
        "estimated_symbol_count": len(symbols) if symbols else _FULL_UNIVERSE_ESTIMATE,
        "symbol_source": symbol_source,
        "universe": request.universe,
        "requested_start": start.strftime("%Y-%m-%d"),
        "requested_end": end.strftime("%Y-%m-%d"),
        "force": request.force,
        "steps": steps,
        "estimated_batches": _estimate_batches(
            steps,
            len(symbols) if symbols else _FULL_UNIVERSE_ESTIMATE,
        ),
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
            instrument_step = _optional_step(plan, "rq.instruments")
            instruments = None
            if instrument_step is not None:
                self._check_cancel(job_id)
                instruments = acquirer.instruments(plan["requested_end"])
                self.store.write("rq.instruments", instruments)
                progress = self._complete_step(job_id, "rq.instruments", progress, total)

            symbols = list(plan["symbols"])
            if request.universe == "all" and not request.symbols:
                if instruments is None:
                    instruments = acquirer.instruments(plan["requested_end"])
                symbols = acquirer.symbols_for_period(
                    instruments,
                    plan["requested_start"],
                    plan["requested_end"],
                )
            if not symbols:
                raise MissingDataError("RQ synchronization resolved an empty stock universe")

            if "bars" in datasets:
                self._check_cancel(job_id)
                step = _step(plan, "rq.bars")
                wrote_bars = False
                bar_groups = _symbol_sync_groups(
                    self.store,
                    ["rq.bars"],
                    symbols,
                    default_start=step["start"],
                    requested_start=plan["requested_start"],
                    instruments=instruments,
                )
                for group_start, group_symbols in bar_groups.items():
                    chunks = _daily_bar_chunks(
                        acquirer,
                        group_symbols,
                        {**step, "start": group_start},
                        progress=lambda message: self.operations.update_job(
                            job_id,
                            message=message,
                        ),
                        cancelled=lambda: self.operations.is_cancel_requested(job_id),
                    )
                    for bars in chunks:
                        self._check_cancel(job_id)
                        self.store.write("rq.bars", bars)
                        wrote_bars = True
                        self.operations.save_checkpoint(
                            job_id,
                            "rq.bars",
                            pd.to_datetime(bars["date"]).max().strftime("%Y-%m-%d"),
                        )
                if not wrote_bars:
                    raise MissingDataError("RQData returned no daily bars")
                self._check_cancel(job_id)
                progress = self._complete_step(job_id, "rq.bars", progress, total)

            if "market-state" in datasets:
                paused_step = _step(plan, "rq.paused")
                st_step = _step(plan, "rq.is_st")
                state_start = min(paused_step["start"], st_step["start"])
                wrote_state = False
                state_groups = _symbol_sync_groups(
                    self.store,
                    ["rq.paused", "rq.is_st"],
                    symbols,
                    default_start=state_start,
                    requested_start=plan["requested_start"],
                    instruments=instruments,
                )
                for group_start, group_symbols in state_groups.items():
                    for paused, is_st in acquirer.market_state_chunks(
                        group_symbols,
                        group_start,
                        paused_step["end"],
                        date_chunk_days=request.market_state_chunk_days,
                        progress=lambda message: self.operations.update_job(
                            job_id,
                            message=message,
                        ),
                        cancelled=lambda: self.operations.is_cancel_requested(job_id),
                    ):
                        self._check_cancel(job_id)
                        self.store.write("rq.paused", paused)
                        self.store.write("rq.is_st", is_st)
                        checkpoint = pd.to_datetime(paused["date"]).max().strftime("%Y-%m-%d")
                        self.operations.save_checkpoint(job_id, "rq.paused", checkpoint)
                        self.operations.save_checkpoint(job_id, "rq.is_st", checkpoint)
                        wrote_state = True
                if not wrote_state:
                    raise MissingDataError("RQData returned no historical market state")
                self._check_cancel(job_id)
                progress = self._complete_step(job_id, "rq.paused", progress, total)
                progress = self._complete_step(job_id, "rq.is_st", progress, total)

                factor_state_step = _step(plan, "rq.daily_factors")
                wrote_daily_factors = False
                factor_requests: dict[tuple[str, tuple[str, ...]], list[str]] = {}
                for field, factor_start in factor_state_step["field_starts"].items():
                    field_groups = _symbol_sync_groups(
                        self.store,
                        ["rq.daily_factors"],
                        symbols,
                        default_start=factor_start,
                        requested_start=plan["requested_start"],
                        instruments=instruments,
                        dimension=("field", field),
                    )
                    for group_start, group_symbols in field_groups.items():
                        key = group_start, tuple(group_symbols)
                        factor_requests.setdefault(key, []).append(field)
                for (factor_start, factor_symbols), fields in factor_requests.items():
                    for daily_factors in acquirer.daily_factor_chunks(
                        list(factor_symbols),
                        fields,
                        factor_start,
                        factor_state_step["end"],
                        date_chunk_days=request.market_state_chunk_days,
                        progress=lambda message: self.operations.update_job(job_id, message=message),
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
                if not wrote_daily_factors:
                    raise MissingDataError("RQData returned no daily factors")
                self._check_cancel(job_id)
                progress = self._complete_step(job_id, "rq.daily_factors", progress, total)

                component_step = _optional_step(plan, "rq.index_components")
                if component_step is not None:
                    for component_start, indexes in _group_starts(
                        component_step["index_starts"]
                    ).items():
                        component_dates = _component_dates(
                            component_start,
                            component_step["end"],
                            component_step["frequency"],
                        )
                        components = acquirer.index_components(
                            indexes,
                            component_dates,
                            progress=lambda message: self.operations.update_job(job_id, message=message),
                            cancelled=lambda: self.operations.is_cancel_requested(job_id),
                        )
                        self._check_cancel(job_id)
                        self.store.write("rq.index_components", components)
                    progress = self._complete_step(
                        job_id,
                        "rq.index_components",
                        progress,
                        total,
                    )

            if "fundamentals" in datasets:
                income_step = _step(plan, "rq.financials.income")
                balance_step = _step(plan, "rq.financials.balance")
                self._check_cancel(job_id)
                income = acquirer.financials(
                    symbols,
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
                    symbols,
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
                self._check_cancel(job_id)
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
                self._check_cancel(job_id)
                factors = build_factor_returns(
                    factor_bars,
                    factor_fundamentals,
                    risk_free,
                )
                if factors.empty:
                    raise MissingDataError(
                        "Runtime factor construction produced no monthly observations"
                    )
                self._check_cancel(job_id)
                self.store.write("runtime.factor_returns", factors)
                progress = self._complete_step(
                    job_id,
                    "runtime.factor_returns",
                    progress,
                    total,
                )

            self._check_cancel(job_id)
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
        self._check_cancel(job_id)
        report = validate_dataset(dataset, self.root)
        if report["status"] != "passed":
            raise DataLoadError(f"Quality validation failed for {dataset}")
        self._check_cancel(job_id)
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


def _stored_universe_symbols(
    store: RuntimeStore,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[str]:
    if store.catalog.status("rq.instruments")["status"] != "ready":
        return []
    frame = store.read("rq.instruments")
    snapshots = pd.to_datetime(frame["snapshot_date"], errors="coerce")
    eligible = snapshots.loc[snapshots.le(end)].dropna()
    snapshot = eligible.max() if not eligible.empty else snapshots.dropna().max()
    if pd.isna(snapshot):
        return []
    frame = frame.loc[snapshots.eq(snapshot)].copy()
    listed = pd.to_datetime(frame.get("listed_date"), errors="coerce")
    delisted = pd.to_datetime(frame.get("de_listed_date"), errors="coerce")
    active = (listed.isna() | listed.le(end)) & (delisted.isna() | delisted.ge(start))
    return sorted(frame.loc[active, "symbol"].dropna().astype(str).unique().tolist())


def _dimension_watermarks(
    store: RuntimeStore,
    dataset: str,
    dimension: str,
) -> dict[str, pd.Timestamp]:
    watermarks: dict[str, pd.Timestamp] = {}
    for path in store.catalog.files(dataset):
        frame = pd.read_parquet(path, columns=["date", dimension])
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for value, latest in frame.dropna(subset=["date", dimension]).groupby(dimension)[
            "date"
        ].max().items():
            key = str(value)
            timestamp = pd.Timestamp(latest).normalize()
            if key not in watermarks or timestamp > watermarks[key]:
                watermarks[key] = timestamp
    return watermarks


def _symbol_sync_groups(
    store: RuntimeStore,
    datasets: list[str],
    symbols: list[str],
    *,
    default_start: str,
    requested_start: str,
    instruments: pd.DataFrame | None,
    dimension: tuple[str, str] | None = None,
) -> dict[str, list[str]]:
    """Keep incremental symbols cheap while backfilling symbols absent from local data."""

    requested = pd.Timestamp(requested_start).normalize()
    default = pd.Timestamp(default_start).normalize()
    if default <= requested:
        return {requested.strftime("%Y-%m-%d"): symbols}

    existing_sets = [
        _stored_dataset_symbols(store, dataset, dimension=dimension)
        for dataset in datasets
    ]
    existing = set.intersection(*existing_sets) if existing_sets else set()
    if not existing:
        return {requested.strftime("%Y-%m-%d"): symbols}

    groups: dict[str, list[str]] = {}
    incremental = [symbol for symbol in symbols if symbol in existing]
    if incremental:
        groups[default.strftime("%Y-%m-%d")] = incremental

    listing_dates: dict[str, pd.Timestamp] = {}
    if instruments is not None and not instruments.empty:
        reference = instruments[["symbol", "listed_date"]].copy()
        reference["listed_date"] = pd.to_datetime(reference["listed_date"], errors="coerce")
        listing_dates = {
            str(symbol): pd.Timestamp(value).normalize()
            for symbol, value in reference.groupby("symbol")["listed_date"].min().items()
            if pd.notna(value)
        }
    for symbol in symbols:
        if symbol in existing:
            continue
        start = max(requested, listing_dates.get(symbol, requested))
        groups.setdefault(start.strftime("%Y-%m-%d"), []).append(symbol)
    return dict(sorted(groups.items()))


def _stored_dataset_symbols(
    store: RuntimeStore,
    dataset: str,
    *,
    dimension: tuple[str, str] | None,
) -> set[str]:
    symbols: set[str] = set()
    dimension_name = dimension[0] if dimension else None
    columns = ["symbol", dimension_name] if dimension_name else ["symbol"]
    for path in store.catalog.files(dataset):
        frame = pd.read_parquet(path, columns=columns)
        if dimension:
            frame = frame.loc[frame[dimension[0]].astype(str).eq(dimension[1])]
        symbols.update(frame["symbol"].dropna().astype(str))
    return symbols


def _date_quarter(value: pd.Timestamp) -> str:
    return f"{value.year}q{value.quarter}"


def _shift_quarter(value: str, offset: int) -> str:
    year = int(value[:4])
    quarter = int(value[-1])
    index = year * 4 + quarter - 1 + offset
    return f"{index // 4}q{index % 4 + 1}"


def _step(plan: dict, dataset: str) -> dict:
    return next(item for item in plan["steps"] if item["dataset"] == dataset)


def _optional_step(plan: dict, dataset: str) -> dict | None:
    return next((item for item in plan["steps"] if item["dataset"] == dataset), None)


def _daily_bar_chunks(
    acquirer: RQAcquirer,
    symbols: list[str],
    step: dict,
    *,
    progress,
    cancelled,
):
    method = getattr(acquirer, "daily_bar_chunks", None)
    if callable(method):
        yield from method(
            symbols,
            step["start"],
            step["end"],
            date_chunk_days=step["date_chunk_days"],
            progress=progress,
            cancelled=cancelled,
        )
        return
    yield acquirer.daily_bars(
        symbols,
        step["start"],
        step["end"],
        progress=progress,
        cancelled=cancelled,
    )


def _component_dates(start: str, end: str, frequency: str) -> list[str]:
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    dates = list(pd.date_range(start_ts, end_ts, freq=frequency))
    dates.append(end_ts)
    return sorted({value.strftime("%Y-%m-%d") for value in dates})


def _group_starts(values: dict[str, str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for name, start in values.items():
        grouped.setdefault(start, []).append(name)
    return grouped


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
            start_year, start_quarter = int(step["start_quarter"][:4]), int(step["start_quarter"][-1])
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
