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
    configured: bool = True


DATASET_SPECS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "rq.instruments",
        "RQ instruments",
        "rq/instruments",
        ("snapshot_date", "symbol"),
        "snapshot_date",
        "rq",
    ),
    DatasetSpec(
        "rq.bars",
        "RQ adjusted daily bars",
        "rq/bars",
        ("date", "symbol"),
        "date",
        "rq",
    ),
    DatasetSpec(
        "rq.financials.income",
        "RQ PIT income statements",
        "rq/financials/statement=income",
        ("symbol", "quarter", "info_date", "if_adjusted"),
        "info_date",
        "rq",
    ),
    DatasetSpec(
        "rq.financials.balance",
        "RQ PIT balance sheets",
        "rq/financials/statement=balance",
        ("symbol", "quarter", "info_date", "if_adjusted"),
        "info_date",
        "rq",
    ),
    DatasetSpec(
        "canonical.fundamentals",
        "Canonical point-in-time fundamentals",
        "canonical/fundamentals",
        ("symbol", "quarter"),
        "available_date",
        "derived",
    ),
    DatasetSpec(
        "runtime.factor_returns",
        "Runtime factor returns",
        "canonical/factor_returns",
        ("date",),
        "date",
        "derived",
        configured=False,
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
            dates: list[pd.Series] = []
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
                    dates.append(pd.to_datetime(frame[spec.date_column], errors="coerce"))
                if "symbol" in frame:
                    symbols.update(frame["symbol"].dropna().astype(str).str.upper())
            combined_dates = pd.concat(dates, ignore_index=True).dropna() if dates else pd.Series(dtype="datetime64[ns]")
            return {
                **base,
                "status": "ready",
                "files": len(files),
                "rows": rows,
                "bytes": sum(path.stat().st_size for path in files),
                "date_start": (
                    pd.Timestamp(combined_dates.min()).strftime("%Y-%m-%d")
                    if not combined_dates.empty
                    else None
                ),
                "date_end": (
                    pd.Timestamp(combined_dates.max()).strftime("%Y-%m-%d")
                    if not combined_dates.empty
                    else None
                ),
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
