"""Runtime dataset catalog and local coverage inspection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Literal

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from alphalab.utils.paths import RUNTIME_DIR

DatasetState = Literal["ready", "missing", "invalid", "not_configured"]


@dataclass(frozen=True)
class DatasetSpec:
    id: str
    label: str
    relative_path: str
    key_columns: tuple[str, ...]
    date_column: str | None
    source: str
    provider_api: tuple[str, ...]
    research_role: str
    field_policy: str
    configured: bool = True


DATASET_SPECS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "rq.instruments",
        "RQ instruments",
        "rq/instruments",
        ("snapshot_date", "symbol"),
        "snapshot_date",
        "rq",
        ("all_instruments",),
        "research_scope",
        "provider_snapshot_schema",
    ),
    DatasetSpec(
        "rq.bars",
        "RQ adjusted and unadjusted daily bars",
        "rq/bars",
        ("date", "symbol"),
        "date",
        "rq",
        ("get_price",),
        "factor_input",
        "provider_daily_schema_plus_raw_ohlc",
    ),
    DatasetSpec(
        "rq.paused",
        "RQ historical suspension state",
        "rq/market_state/paused",
        ("date", "symbol"),
        "date",
        "rq",
        ("is_suspended", "get_price"),
        "execution_input",
        "boolean_daily_state",
    ),
    DatasetSpec(
        "rq.is_st",
        "RQ historical ST state",
        "rq/market_state/is_st",
        ("date", "symbol"),
        "date",
        "rq",
        ("is_st_stock",),
        "strategy_input",
        "boolean_daily_state",
    ),
    DatasetSpec(
        "rq.daily_factors",
        "RQ daily point-in-time factors",
        "rq/market_state/daily_factors",
        ("date", "symbol", "field"),
        "date",
        "rq",
        ("get_factor",),
        "point_in_time_factor_input",
        "long_daily_field_value",
    ),
    DatasetSpec(
        "rq.index_components",
        "RQ historical index components",
        "rq/market_state/index_components",
        ("date", "index_symbol", "symbol"),
        "date",
        "rq",
        ("index_components",),
        "point_in_time_universe_input",
        "dated_membership_snapshot",
    ),
    DatasetSpec(
        "rq.financials.income",
        "RQ PIT income statements",
        "rq/financials/statement=income",
        ("symbol", "quarter", "info_date", "if_adjusted"),
        "info_date",
        "rq",
        ("get_pit_financials_ex",),
        "point_in_time_staging",
        "configured_statement_fields",
    ),
    DatasetSpec(
        "rq.financials.balance",
        "RQ PIT balance sheets",
        "rq/financials/statement=balance",
        ("symbol", "quarter", "info_date", "if_adjusted"),
        "info_date",
        "rq",
        ("get_pit_financials_ex",),
        "point_in_time_staging",
        "configured_statement_fields",
    ),
    DatasetSpec(
        "canonical.fundamentals",
        "Canonical point-in-time fundamentals",
        "canonical/fundamentals",
        ("symbol", "quarter"),
        "available_date",
        "derived",
        ("get_pit_financials_ex", "get_price"),
        "factor_input",
        "schema_discovered_canonical_fields",
    ),
    DatasetSpec(
        "runtime.factor_returns",
        "Runtime factor returns",
        "canonical/factor_returns",
        ("date",),
        "date",
        "derived",
        ("get_yield_curve", "get_price", "get_pit_financials_ex"),
        "attribution_input",
        "derived_factor_return_schema",
    ),
)
DATASETS = {spec.id: spec for spec in DATASET_SPECS}


@dataclass(frozen=True)
class _PartitionSummary:
    rows: int
    date_start: str | None
    date_end: str | None
    symbols: frozenset[str]


_PARTITION_LOCK = RLock()


@lru_cache(maxsize=512)
def _partition_summary(
    path: str, mtime_ns: int, ctime_ns: int, size: int, date_column: str | None,
) -> _PartitionSummary:
    """Cache compact coverage only; file replacement changes the cache key.

    Arrow reduces dates and symbols before conversion to Python. Reading these
    columns never loads price/state frames or sorts the full market history.
    """
    parquet = pq.ParquetFile(path)
    columns = [name for name in (date_column, "symbol") if name in parquet.schema_arrow.names]
    table = parquet.read(columns=columns)
    symbols = frozenset(
        str(value).upper() for value in pc.unique(table["symbol"]).to_pylist()
        if value is not None
    ) if "symbol" in columns else frozenset()
    start = end = None
    if date_column in columns:
        values = table[date_column]
        if pa.types.is_timestamp(values.type) or pa.types.is_date(values.type):
            bounds = pc.min_max(values).as_py()
            minimum, maximum = bounds["min"], bounds["max"]
        else:
            dates = pd.to_datetime(values.to_pandas(), errors="coerce").dropna()
            minimum, maximum = (dates.min(), dates.max()) if not dates.empty else (None, None)
        if minimum is not None and maximum is not None:
            start = pd.Timestamp(minimum).strftime("%Y-%m-%d")
            end = pd.Timestamp(maximum).strftime("%Y-%m-%d")
    return _PartitionSummary(parquet.metadata.num_rows, start, end, symbols)


class DataCatalog:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else RUNTIME_DIR

    @staticmethod
    def clear_cache() -> None:
        """Discard shared coverage metadata when an explicit refresh is requested."""
        with _PARTITION_LOCK:
            _partition_summary.cache_clear()

    def spec(self, dataset: str) -> DatasetSpec:
        try:
            return DATASETS[dataset]
        except KeyError as exc:
            raise KeyError(f"Unknown runtime dataset: {dataset}") from exc

    def path(self, dataset: str) -> Path:
        return self.root / self.spec(dataset).relative_path

    def files(
        self, dataset: str, *, start: str | None = None, end: str | None = None,
    ) -> list[Path]:
        files = sorted(self.path(dataset).rglob("*.parquet"))
        if start is None and end is None:
            return files
        lower = pd.Timestamp(start).strftime("%Y-%m-%d") if start is not None else None
        upper = pd.Timestamp(end).strftime("%Y-%m-%d") if end is not None else None
        selected = []
        for path in files:
            info = self._partition_info(dataset, path)
            if lower and info.date_end and info.date_end < lower:
                continue
            if upper and info.date_start and info.date_start > upper:
                continue
            selected.append(path)
        return selected

    def _partition_info(self, dataset: str, path: Path) -> _PartitionSummary:
        stat = path.stat()
        # Serialize cache misses so concurrent widgets do not scan the same
        # partition twice. All callers receive an immutable cached value.
        with _PARTITION_LOCK:
            return _partition_summary(
                str(path.resolve()), stat.st_mtime_ns, stat.st_ctime_ns,
                stat.st_size, self.spec(dataset).date_column,
            )

    def symbols(self, dataset: str) -> list[str]:
        """Return actual stored symbols, including historical/delisted symbols."""
        symbols: set[str] = set()
        for path in self.files(dataset):
            symbols.update(self._partition_info(dataset, path).symbols)
        return sorted(symbols)

    def status(self, dataset: str) -> dict:
        spec = self.spec(dataset)
        base = {
            **asdict(spec),
            "path": str(self.path(dataset)),
            "files": 0,
            "rows": 0,
            "bytes": 0,
            "date_start": None,
            "date_end": None,
            "symbol_count": 0,
            "error": None,
        }
        if not spec.configured:
            return {**base, "status": "not_configured"}
        files = self.files(dataset)
        if not files:
            return {**base, "status": "missing"}
        try:
            rows = 0
            date_start: str | None = None
            date_end: str | None = None
            symbols: set[str] = set()
            for path in files:
                info = self._partition_info(dataset, path)
                rows += info.rows
                if info.date_start is not None:
                    date_start = min(date_start, info.date_start) if date_start else info.date_start
                if info.date_end is not None:
                    date_end = max(date_end, info.date_end) if date_end else info.date_end
                symbols.update(info.symbols)
            return {
                **base,
                "status": "ready",
                "files": len(files),
                "rows": rows,
                "bytes": sum(path.stat().st_size for path in files),
                "date_start": date_start,
                "date_end": date_end,
                "symbol_count": len(symbols),
            }
        except Exception as exc:
            return {**base, "status": "invalid", "files": len(files), "error": str(exc)}

    def list(self) -> list[dict]:
        return [self.status(spec.id) for spec in DATASET_SPECS]

    def summary(self) -> dict:
        datasets = self.list()
        ready = sum(item["status"] == "ready" for item in datasets)
        configured = sum(item["configured"] for item in datasets)
        state = "ready" if ready == configured else ("partial" if ready else "missing")
        return {
            "root": str(self.root),
            "status": state,
            "ready": ready,
            "total": len(datasets),
            "configured": configured,
            "datasets": datasets,
        }


__all__ = ["DATASETS", "DATASET_SPECS", "DataCatalog", "DatasetSpec", "DatasetState"]
