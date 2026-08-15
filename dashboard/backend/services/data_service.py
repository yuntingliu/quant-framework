"""Profile-aware data queries used by the workstation API."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from alphalab import ResultStore, create_default_engine, create_runtime_engine
from alphalab.dataio import DataEngine, MissingDataError
from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.fundamentals import CANONICAL_FIELDS
from alphalab.utils.paths import APP_DATA_DIR, DATA_DIR, FACTOR_DIR, FUNDAMENTAL_DIR, MARKET_DIR


FUNDAMENTAL_FIELDS = ("shares", "market_cap", *CANONICAL_FIELDS)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest() -> dict:
    path = DATA_DIR / "manifest.json"
    if not path.exists():
        raise MissingDataError(f"Bundled data manifest is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset_status(path: Path, manifest: dict, *, mutable: bool = False) -> dict:
    relative = path.relative_to(DATA_DIR.parent).as_posix()
    expected = manifest.get("files", {}).get(relative, {})
    if not path.exists():
        return {"status": "missing", "path": relative, "bytes": 0}
    actual_hash = _hash_file(path)
    expected_hash = expected.get("sha256")
    return {
        "status": "ready" if mutable or not expected_hash or actual_hash == expected_hash else "invalid",
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": actual_hash,
    }


def list_provider_status() -> dict:
    manifest = load_manifest()
    engine = create_default_engine()
    runtime = DataCatalog().summary()
    runtime_factor_status = next(
        (item["status"] for item in runtime["datasets"] if item["id"] == "runtime.factor_returns"),
        "missing",
    )
    return {
        "providers": engine.providers(),
        "active_profile": "demo",
        "profiles": {
            "demo": {
                "status": "ready",
                "latest_date": engine.get_latest_date(),
                "symbol_count": manifest.get("symbol_count", 0),
                "factor_returns": "ready",
            },
            "runtime": {
                "status": runtime["status"],
                "latest_date": next(
                    (item["date_end"] for item in runtime["datasets"] if item["id"] == "rq.bars"),
                    None,
                ),
                "symbol_count": next(
                    (item["symbol_count"] for item in runtime["datasets"] if item["id"] == "rq.bars"),
                    0,
                ),
                "factor_returns": runtime_factor_status,
            },
        },
        "latest_date": engine.get_latest_date(),
        "sample_start": manifest.get("sample_start"),
        "symbol_count": manifest.get("symbol_count", 0),
        "realtime": {"status": "not_configured", "source": None},
        "datasets": {
            "market": _dataset_status(MARKET_DIR / "bars.parquet", manifest),
            "fundamentals": _dataset_status(FUNDAMENTAL_DIR / "fundamentals.parquet", manifest),
            "factors": _dataset_status(FACTOR_DIR / "factor_returns.parquet", manifest),
            "app": _dataset_status(APP_DATA_DIR / "alphalab.db", manifest, mutable=True),
        },
        "runtime": runtime,
    }


def _engine(profile: str) -> DataEngine:
    if profile == "demo":
        return create_default_engine()
    if profile == "runtime":
        return create_runtime_engine()
    raise ValueError("profile must be demo or runtime")


def _profile_range(profile: str) -> tuple[str, str]:
    if profile == "demo":
        manifest = load_manifest()
        return manifest["sample_start"], manifest["cutoff_date"]
    status = DataCatalog().status("rq.bars")
    if status["status"] != "ready" or not status["date_start"] or not status["date_end"]:
        raise MissingDataError("Runtime bars are not ready. Run an RQ data sync first.")
    return status["date_start"], status["date_end"]


def market_symbol_options(profile: str = "demo") -> list[dict[str, str | None]]:
    engine = _engine(profile)
    symbols = engine.get_symbols()
    if profile == "runtime" and not symbols:
        raise MissingDataError("Runtime bars are not ready. Run an RQ data sync first.")
    names: dict[str, str] = {}
    if profile == "demo":
        names.update(
            {
                str(symbol).strip().upper(): str(name).strip()
                for symbol, name in load_manifest().get("symbol_names", {}).items()
                if str(symbol).strip() and str(name).strip()
            }
        )
    instruments = engine.get_instruments(engine.get_latest_date())
    if not instruments.empty and "symbol" in instruments:
        name_column = next((column for column in ("name", "display_name") if column in instruments), None)
        if name_column:
            for symbol, name in instruments[["symbol", name_column]].itertuples(index=False):
                normalized_symbol = str(symbol).strip().upper()
                normalized_name = "" if pd.isna(name) else str(name).strip()
                if normalized_symbol and normalized_name:
                    names[normalized_symbol] = normalized_name
    return [{"symbol": symbol, "name": names.get(symbol)} for symbol in symbols]


def market_bars(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    profile: str = "demo",
) -> list[dict]:
    engine = _engine(profile)
    normalized = symbol.strip().upper()
    if normalized not in engine.get_symbols():
        raise KeyError(normalized)
    profile_start, profile_end = _profile_range(profile)
    start = start or profile_start
    end = end or profile_end
    if pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError("start must be on or before end")
    frame = engine.get_bars([normalized], start, end, strict=True, use_cache=False)
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    return frame.to_dict("records")


def fundamentals(
    symbols: list[str],
    fields: list[str] | None = None,
    start_quarter: str | None = None,
    end_quarter: str | None = None,
    asof_date: str | None = None,
    profile: str = "demo",
    limit: int = 100,
) -> dict:
    engine = _engine(profile)
    normalized_symbols = list(
        dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip())
    )
    if not normalized_symbols:
        raise ValueError("at least one symbol is required")
    unknown_symbols = sorted(set(normalized_symbols) - set(engine.get_symbols()))
    if unknown_symbols:
        raise KeyError(f"Unknown symbols: {unknown_symbols}")
    requested_fields = list(dict.fromkeys(fields or FUNDAMENTAL_FIELDS))
    unknown_fields = sorted(set(requested_fields) - set(FUNDAMENTAL_FIELDS))
    if unknown_fields:
        raise KeyError(f"Unknown fundamental fields: {unknown_fields}")
    profile_start, profile_end = _profile_range(profile)
    start_quarter = (start_quarter or _quarter_label(profile_start)).lower()
    end_quarter = (end_quarter or _quarter_label(profile_end)).lower()
    if start_quarter > end_quarter:
        raise ValueError("start_quarter must be on or before end_quarter")
    effective_asof = asof_date or profile_end
    frame = engine.get_fundamentals(
        normalized_symbols,
        requested_fields,
        start_quarter,
        end_quarter,
        asof_date=effective_asof,
        strict=True,
        use_cache=False,
    )
    preview = frame.head(limit).copy()
    if "available_date" in preview:
        preview["available_date"] = pd.to_datetime(preview["available_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return {
        "profile": profile,
        "symbols": normalized_symbols,
        "fields": requested_fields,
        "start_quarter": start_quarter,
        "end_quarter": end_quarter,
        "asof_date": effective_asof,
        "matched_rows": len(frame),
        "returned_rows": len(preview),
        "truncated": len(frame) > len(preview),
        "rows": preview.where(pd.notna(preview), None).to_dict("records"),
    }


def factor_returns(
    names: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    profile: str = "demo",
) -> dict:
    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
    available = {"MKT", "SMB", "HML", "MOM", "RMW", "rf"}
    requested = names or ["MKT", "SMB", "HML", "MOM", "RMW", "rf"]
    unknown = sorted(set(requested) - available)
    if unknown:
        raise KeyError(", ".join(unknown))
    if profile == "runtime" and DataCatalog().status("runtime.factor_returns")["status"] != "ready":
        raise MissingDataError("Runtime factor returns are missing. Run an RQ factors sync first.")
    profile_start, profile_end = _profile_range(profile)
    frame = _engine(profile).get_factors(
        requested,
        start or profile_start,
        end or profile_end,
        strict=True,
        use_cache=False,
    )
    values = frame.reset_index().rename(columns={frame.index.name or "index": "date"})
    values["date"] = pd.to_datetime(values["date"]).dt.strftime("%Y-%m-%d")
    return {"names": requested, "rows": values.to_dict("records")}


def _quarter_label(value: str) -> str:
    timestamp = pd.Timestamp(value)
    return f"{timestamp.year}q{timestamp.quarter}"


def store_stats() -> dict:
    store = ResultStore()
    try:
        return store.stats()
    finally:
        store.close()


__all__ = [
    "_engine",
    "_profile_range",
    "factor_returns",
    "fundamentals",
    "list_provider_status",
    "load_manifest",
    "market_bars",
    "market_symbol_options",
    "store_stats",
]
