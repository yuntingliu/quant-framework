"""Structured validation for runtime datasets."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from alphalab.dataio.catalog import DATASETS
from alphalab.dataio.runtime import RuntimeStore


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    severity: str = "error"


def validate_dataset(dataset: str, root: str | Path | None = None) -> dict:
    if dataset not in DATASETS:
        raise KeyError(f"Unknown runtime dataset: {dataset}")
    store = RuntimeStore(root)
    status = store.catalog.status(dataset)
    issues: list[QualityIssue] = []
    if status["status"] != "ready":
        issues.append(QualityIssue(status["status"], status.get("error") or "Dataset is not ready"))
        report = _report(dataset, status, issues)
        store.operations.record_quality(dataset, report)
        return report

    frame = store.read(dataset)
    spec = DATASETS[dataset]
    missing = [name for name in spec.key_columns if name not in frame]
    if missing:
        issues.append(QualityIssue("missing_columns", f"Missing key columns: {missing}"))
    elif frame.duplicated(list(spec.key_columns)).any():
        issues.append(QualityIssue("duplicate_keys", "Dataset contains duplicate primary keys"))

    if dataset == "rq.bars":
        required = {"date", "symbol", "open", "high", "low", "close", "raw_close", "volume", "amount"}
        missing_bars = sorted(required - set(frame))
        if missing_bars:
            issues.append(QualityIssue("missing_columns", f"Missing bar columns: {missing_bars}"))
        else:
            numeric = frame[list(required - {"date", "symbol"})].apply(pd.to_numeric, errors="coerce")
            if numeric[["open", "high", "low", "close", "raw_close"]].isna().any().any():
                issues.append(QualityIssue("invalid_prices", "Price columns contain null or non-numeric values"))
            invalid_ohlc = (
                (numeric["high"] < numeric["low"])
                | (numeric["high"] < numeric[["open", "close"]].max(axis=1))
                | (numeric["low"] > numeric[["open", "close"]].min(axis=1))
                | (numeric[["open", "high", "low", "close", "raw_close"]] <= 0).any(axis=1)
            )
            if invalid_ohlc.any():
                issues.append(QualityIssue("invalid_ohlc", f"{int(invalid_ohlc.sum())} invalid OHLC rows"))
    elif dataset.startswith("rq.financials."):
        for column in ("quarter", "symbol", "info_date", "if_adjusted"):
            if column not in frame:
                issues.append(QualityIssue("missing_columns", f"Missing PIT field: {column}"))
        if "info_date" in frame and pd.to_datetime(frame["info_date"], errors="coerce").isna().any():
            issues.append(QualityIssue("invalid_disclosure_date", "PIT rows contain invalid info_date"))
    elif dataset == "canonical.fundamentals":
        required = {
            "quarter",
            "available_date",
            "symbol",
            "ep",
            "bp",
            "roe",
            "gross_margin",
            "leverage",
            "profit_growth",
            "revenue_growth",
        }
        missing_fundamentals = sorted(required - set(frame))
        if missing_fundamentals:
            issues.append(
                QualityIssue("missing_columns", f"Missing canonical columns: {missing_fundamentals}")
            )
        elif pd.to_datetime(frame["available_date"], errors="coerce").isna().any():
            issues.append(QualityIssue("invalid_available_date", "Canonical rows need available_date"))
    report = _report(dataset, status, issues)
    store.operations.record_quality(dataset, report)
    return report


def validate_all(root: str | Path | None = None) -> list[dict]:
    return [
        validate_dataset(dataset, root)
        for dataset, spec in DATASETS.items()
        if spec.configured
    ]


def _report(dataset: str, status: dict, issues: list[QualityIssue]) -> dict:
    return {
        "dataset": dataset,
        "status": "failed" if any(item.severity == "error" for item in issues) else "passed",
        "rows": status.get("rows", 0),
        "files": status.get("files", 0),
        "issues": [asdict(item) for item in issues],
    }


__all__ = ["QualityIssue", "validate_all", "validate_dataset"]
