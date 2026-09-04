"""Runtime dataset catalog and local coverage inspection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
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


class DataCatalog:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else RUNTIME_DIR

    def spec(self, dataset: str) -> DatasetSpec:
        try:
            return DATASETS[dataset]
        except KeyError as exc:
            raise KeyError(f"Unknown runtime dataset: {dataset}") from exc

    def path(self, dataset: str) -> Path:
        return self.root / self.spec(dataset).relative_path

    def files(self, dataset: str) -> list[Path]:
        return sorted(self.path(dataset).rglob("*.parquet"))

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
            rows = sum(int(pq.ParquetFile(path).metadata.num_rows) for path in files)
            date_start: pd.Timestamp | None = None
            date_end: pd.Timestamp | None = None
            symbols: set[str] = set()
            for path in files:
                schema = pq.read_schema(path)
                columns: list[str] = []
                if spec.date_column and spec.date_column in schema.names:
                    columns.append(spec.date_column)
                if "symbol" in schema.names:
                    columns.append("symbol")
                if not columns:
                    continue
                frame = pd.read_parquet(path, columns=columns)
                if spec.date_column and spec.date_column in frame:
                    dates = pd.to_datetime(frame[spec.date_column], errors="coerce").dropna()
                    if not dates.empty:
                        current_start = pd.Timestamp(dates.min())
                        current_end = pd.Timestamp(dates.max())
                        date_start = (
                            current_start if date_start is None else min(date_start, current_start)
                        )
                        date_end = current_end if date_end is None else max(date_end, current_end)
                if "symbol" in frame:
                    symbols.update(frame["symbol"].dropna().astype(str).str.upper())
            return {
                **base,
                "status": "ready",
                "files": len(files),
                "rows": rows,
                "bytes": sum(path.stat().st_size for path in files),
                "date_start": (date_start.strftime("%Y-%m-%d") if date_start is not None else None),
                "date_end": (date_end.strftime("%Y-%m-%d") if date_end is not None else None),
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
