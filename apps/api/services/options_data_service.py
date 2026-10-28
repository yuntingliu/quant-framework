"""Read-only option coverage evidence and explicitly managed export archives."""

import json
import re
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import pandas as pd
import pyarrow.parquet as pq

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.errors import MissingDataError
from alphalab.options.contracts import contract_names
from alphalab.utils.paths import REPO_ROOT
from apps.api.services.options_service import ETF_NAMES

ARCHIVE_NAME = re.compile(r"ETF_Options_Data_\d{8}-\d{8}(?:_\d{6})?\.zip\Z")


def archive_path(name, root=None):
    if not ARCHIVE_NAME.fullmatch(name):
        raise ValueError("Invalid option export filename")
    repo = Path(root or REPO_ROOT).resolve()
    base = (repo / "artifacts" / "exports").resolve()
    path = (base / name).resolve()
    if not base.is_relative_to(repo) or not path.is_relative_to(base) or not path.is_file():
        raise MissingDataError("Saved option data export is unavailable")
    return path


def exports(root=None):
    repo = Path(root or REPO_ROOT).resolve()
    base = (repo / "artifacts" / "exports").resolve()
    if not base.is_relative_to(repo):
        return {"archives": []}
    result = []
    for candidate in sorted(base.glob("ETF_Options_Data_*.zip"), reverse=True)[:100]:
        try:
            path = archive_path(candidate.name, root)
            with ZipFile(path) as z:
                info = z.getinfo("manifest.json")
                if info.file_size > 2_000_000:
                    continue
                manifest = json.loads(z.read(info))
            if not isinstance(manifest, dict):
                continue
            period = manifest.get("main_period")
            datasets = manifest.get("datasets")
            snapshot = datasets.get("rq.option_snapshots") if isinstance(datasets, dict) else None
            rows = snapshot.get("rows") if isinstance(snapshot, dict) else None
            if (
                not isinstance(period, list)
                or len(period) != 2
                or not all(
                    isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in period
                )
                or type(rows) is not int
                or rows < 0
            ):
                continue
            sidecar = path.with_suffix(".zip.sha256").resolve()
            digest = None
            if sidecar.is_relative_to(base) and sidecar.is_file():
                with sidecar.open(encoding="utf-8") as stream:
                    parts = stream.read(100).split()
                digest = parts[0] if parts else None
            if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
                digest = None
            result.append(
                {
                    "filename": path.name,
                    "bytes": path.stat().st_size,
                    "start": period[0],
                    "end": period[1],
                    "rows": rows,
                    "created_at": manifest.get("created_at")
                    if isinstance(manifest.get("created_at"), str)
                    else None,
                    "sha256": digest,
                }
            )
        except (BadZipFile, OSError, KeyError, ValueError, MissingDataError, IndexError):
            continue
    return {
        "archives": sorted(
            result, key=lambda r: (r["end"], r["rows"], r["created_at"] or ""), reverse=True
        )
    }


@lru_cache(maxsize=32768)
def _has_quarantine(path, size, mtime):
    return "quote_quarantine" in pq.ParquetFile(path).schema_arrow.names


def quarantine(*, underlying=None, limit=100, root=None):
    if underlying is not None and underlying not in ETF_NAMES:
        raise ValueError("Select a supported ETF option underlying")
    if not 1 <= limit <= 500:
        raise ValueError("Quarantine limit must be between 1 and 500")
    catalog = DataCatalog(root)
    rows, total = [], 0
    for path in reversed(catalog.files("rq.option_snapshots")):
        symbol = path.parent.name.removeprefix("underlying=")
        if underlying is not None and symbol != underlying:
            continue
        stat = path.stat()
        if not _has_quarantine(str(path), stat.st_size, stat.st_mtime_ns):
            continue
        columns = [
            "date",
            "underlying",
            "order_book_id",
            "maturity_date",
            "option_type",
            "strike_price",
            "contract_multiplier",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_quarantine",
            "quote_quarantine_reason",
        ]
        frame = pq.ParquetFile(path).read(columns=columns).to_pandas()
        flagged = frame.loc[frame.quote_quarantine.notna()]
        total += len(flagged)
        for row in flagged.head(max(0, limit - len(rows))).to_dict("records"):
            names = contract_names(
                row["underlying"],
                str(pd.Timestamp(row["maturity_date"]).date()),
                row["option_type"],
                row["strike_price"],
                row["contract_multiplier"],
            )
            rows.append(
                {
                    "date": str(pd.Timestamp(row["date"]).date()),
                    "underlying": row["underlying"],
                    "contract": str(row["order_book_id"]),
                    **names,
                    **{key: float(row[key]) for key in ("open", "high", "low", "close", "volume")},
                    "reason": str(row["quote_quarantine_reason"]),
                }
            )
    return {
        "total": total,
        "rows": rows,
        "limit": limit,
        "scope": underlying or "all_supported_etfs",
    }
