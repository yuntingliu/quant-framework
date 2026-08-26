"""Structured validation for runtime datasets."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from alphalab.dataio.catalog import DATASETS
from alphalab.dataio.runtime import RuntimeStore


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    severity: str = "error"


def validate_dataset(
    dataset: str,
    root: str | Path | None = None,
    *,
    start_date: str | None = None,
    as_of_date: str | None = None,
    fail_on_gap: bool = False,
) -> dict:
    if dataset not in DATASETS:
        raise KeyError(f"Unknown runtime dataset: {dataset}")
    store = RuntimeStore(root)
    status = store.catalog.status(dataset)
    issues: list[QualityIssue] = []
    if status["status"] != "ready":
        issues.append(QualityIssue(status["status"], status.get("error") or "Dataset is not ready"))
        report = _report(dataset, status, issues, {})
        store.operations.record_quality(dataset, report)
        return report

    if dataset in {"rq.bars", "rq.paused", "rq.is_st", "rq.daily_factors"}:
        issues, metrics = _validate_large_daily_dataset(
            store,
            dataset,
            start_date=start_date,
            as_of_date=as_of_date,
            fail_on_gap=fail_on_gap,
        )
        report = _report(dataset, status, issues, metrics)
        store.operations.record_quality(dataset, report)
        return report

    frame = store.read(dataset)
    spec = DATASETS[dataset]
    metrics = _coverage_metrics(frame, spec.date_column)
    missing = [name for name in spec.key_columns if name not in frame]
    if missing:
        issues.append(QualityIssue("missing_columns", f"Missing key columns: {missing}"))
    elif frame.duplicated(list(spec.key_columns)).any():
        issues.append(QualityIssue("duplicate_keys", "Dataset contains duplicate primary keys"))

    if dataset == "rq.index_components":
        required = {"date", "index_symbol", "symbol"}
        missing_components = sorted(required - set(frame))
        if missing_components:
            issues.append(
                QualityIssue("missing_columns", f"Missing index-component columns: {missing_components}")
            )
        else:
            dated_components = frame.assign(
                date=pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
            )
            index_ranges = dated_components.groupby("index_symbol")["date"].agg(["min", "max"])
            metrics["indexes"] = sorted(index_ranges.index.astype(str).tolist())
            metrics["index_start_dates"] = {
                str(name): value.strftime("%Y-%m-%d")
                for name, value in index_ranges["min"].items()
                if pd.notna(value)
            }
            metrics["index_end_dates"] = {
                str(name): value.strftime("%Y-%m-%d")
                for name, value in index_ranges["max"].items()
                if pd.notna(value)
            }
            if as_of_date:
                expected = pd.Timestamp(as_of_date).normalize()
                metrics["as_of_date"] = expected.strftime("%Y-%m-%d")
                stale_indexes = [
                    str(name)
                    for name, value in index_ranges["max"].items()
                    if pd.isna(value) or value < expected
                ]
                if fail_on_gap and stale_indexes:
                    issues.append(
                        QualityIssue(
                            "stale_index_end_dates",
                            "Index snapshots do not reach the required date: "
                            + ", ".join(sorted(stale_indexes)),
                        )
                    )
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
    elif dataset == "runtime.factor_returns":
        required = {"date", "MKT", "SMB", "HML", "MOM", "RMW", "rf"}
        missing_factors = sorted(required - set(frame))
        if missing_factors:
            issues.append(
                QualityIssue("missing_columns", f"Missing factor columns: {missing_factors}")
            )
        else:
            values = frame[list(required - {"date"})].apply(pd.to_numeric, errors="coerce")
            if values[["MKT", "rf"]].isna().any().any():
                issues.append(
                    QualityIssue("invalid_factor_returns", "MKT and rf must be finite")
                )
            dates = pd.to_datetime(frame["date"], errors="coerce")
            if dates.isna().any():
                issues.append(QualityIssue("invalid_dates", "Factor dates are invalid"))
    report = _report(dataset, status, issues, metrics)
    store.operations.record_quality(dataset, report)
    return report


def validate_all(
    root: str | Path | None = None,
    *,
    datasets: Iterable[str] | None = None,
    start_date: str | None = None,
    as_of_date: str | None = None,
    fail_on_gap: bool = False,
) -> list[dict]:
    selected = (
        list(dict.fromkeys(str(dataset).strip() for dataset in datasets if str(dataset).strip()))
        if datasets is not None
        else [dataset for dataset, spec in DATASETS.items() if spec.configured]
    )
    if not selected:
        raise ValueError("At least one dataset is required")
    return [
        validate_dataset(
            dataset,
            root,
            start_date=start_date,
            as_of_date=as_of_date,
            fail_on_gap=fail_on_gap,
        )
        for dataset in selected
    ]


def _coverage_metrics(frame: pd.DataFrame, date_column: str | None) -> dict:
    metrics = {
        "distinct_dates": 0,
        "start_date": None,
        "end_date": None,
        "symbol_count": int(frame["symbol"].nunique()) if "symbol" in frame else 0,
    }
    if not date_column or date_column not in frame:
        return metrics
    dates = pd.to_datetime(frame[date_column], errors="coerce").dropna().dt.normalize()
    if dates.empty:
        return metrics
    metrics.update(
        {
            "distinct_dates": int(dates.nunique()),
            "start_date": dates.min().strftime("%Y-%m-%d"),
            "end_date": dates.max().strftime("%Y-%m-%d"),
        }
    )
    return metrics


def _validate_large_daily_dataset(
    store: RuntimeStore,
    dataset: str,
    *,
    start_date: str | None,
    as_of_date: str | None,
    fail_on_gap: bool,
) -> tuple[list[QualityIssue], dict]:
    if dataset == "rq.bars":
        return _validate_partitioned_bars(
            store,
            start_date=start_date,
            as_of_date=as_of_date,
            fail_on_gap=fail_on_gap,
        )
    if dataset in {"rq.paused", "rq.is_st"}:
        return _validate_partitioned_state(
            store,
            dataset,
            start_date=start_date,
            as_of_date=as_of_date,
            fail_on_gap=fail_on_gap,
        )
    return _validate_partitioned_factors(
        store,
        start_date=start_date,
        as_of_date=as_of_date,
        fail_on_gap=fail_on_gap,
    )


def _validate_partitioned_bars(
    store: RuntimeStore,
    *,
    start_date: str | None,
    as_of_date: str | None,
    fail_on_gap: bool,
) -> tuple[list[QualityIssue], dict]:
    required = {
        "date",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "raw_close",
        "volume",
        "amount",
    }
    dates: set[pd.Timestamp] = set()
    symbols: set[str] = set()
    missing_columns: set[str] = set()
    duplicate_count = 0
    invalid_date_count = 0
    invalid_price_count = 0
    invalid_activity_count = 0
    invalid_ohlc_count = 0
    numeric_columns = list(required - {"date", "symbol"})
    for path in store.catalog.files("rq.bars"):
        frame = pd.read_parquet(path)
        missing = required - set(frame)
        if missing:
            missing_columns.update(missing)
            continue
        normalized_dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        invalid_date_count += int(normalized_dates.isna().sum())
        dates.update(normalized_dates.dropna().tolist())
        symbols.update(frame["symbol"].dropna().astype(str).tolist())
        duplicate_count += int(frame.duplicated(["date", "symbol"]).sum())
        numeric = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
        prices = numeric[["open", "high", "low", "close", "raw_close"]]
        invalid_price_count += int((~np.isfinite(prices)).any(axis=1).sum())
        activity = numeric[["volume", "amount"]]
        invalid_activity_count += int(
            ((~np.isfinite(activity)) | activity.lt(0)).any(axis=1).sum()
        )
        invalid_ohlc_count += int(
            (
                (numeric["high"] < numeric["low"])
                | (numeric["high"] < numeric[["open", "close"]].max(axis=1))
                | (numeric["low"] > numeric[["open", "close"]].min(axis=1))
                | (prices <= 0).any(axis=1)
            ).sum()
        )
    metrics = _set_coverage_metrics(dates, symbols)
    issues = _partition_issues(
        missing_columns=missing_columns,
        duplicate_count=duplicate_count,
        invalid_date_count=invalid_date_count,
    )
    if invalid_price_count:
        issues.append(
            QualityIssue(
                "invalid_prices",
                f"Price columns contain {invalid_price_count} null or non-finite rows",
            )
        )
    if invalid_activity_count:
        issues.append(
            QualityIssue(
                "invalid_activity",
                f"Volume/amount contain {invalid_activity_count} invalid rows",
            )
        )
    if invalid_ohlc_count:
        issues.append(QualityIssue("invalid_ohlc", f"{invalid_ohlc_count} invalid OHLC rows"))
    _apply_bar_bounds(
        metrics,
        issues,
        start_date=start_date,
        as_of_date=as_of_date,
        fail_on_gap=fail_on_gap,
    )
    return issues, metrics


def _validate_partitioned_state(
    store: RuntimeStore,
    dataset: str,
    *,
    start_date: str | None,
    as_of_date: str | None,
    fail_on_gap: bool,
) -> tuple[list[QualityIssue], dict]:
    field = dataset.split(".")[-1]
    required = {"date", "symbol", field}
    dates: set[pd.Timestamp] = set()
    symbols: set[str] = set()
    missing_columns: set[str] = set()
    duplicate_count = 0
    invalid_date_count = 0
    invalid_state_count = 0
    state_files: dict[tuple[str, str], Path] = {}
    for path in store.catalog.files(dataset):
        frame = pd.read_parquet(path)
        missing = required - set(frame)
        if missing:
            missing_columns.update(missing)
            continue
        normalized_dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        invalid_date_count += int(normalized_dates.isna().sum())
        dates.update(normalized_dates.dropna().tolist())
        symbols.update(frame["symbol"].dropna().astype(str).tolist())
        duplicate_count += int(frame.duplicated(["date", "symbol"]).sum())
        try:
            invalid_state_count += int(frame[field].astype("boolean").isna().sum())
        except (TypeError, ValueError):
            invalid_state_count += len(frame)
        state_files[_partition_key(path)] = path
    metrics = _set_coverage_metrics(dates, symbols)
    issues = _partition_issues(
        missing_columns=missing_columns,
        duplicate_count=duplicate_count,
        invalid_date_count=invalid_date_count,
    )
    if invalid_state_count:
        issues.append(
            QualityIssue(
                "invalid_state",
                f"{field} contains {invalid_state_count} null or invalid rows",
            )
        )
    coverage = _partitioned_bar_key_coverage(
        store,
        state_files,
        start_date=start_date,
        as_of_date=as_of_date,
    )
    metrics["bar_key_coverage"] = coverage
    if fail_on_gap and coverage < 0.98:
        issues.append(
            QualityIssue(
                "bar_key_gap",
                f"{field} covers only {coverage:.2%} of required RQ bar keys",
            )
        )
    return issues, metrics


def _validate_partitioned_factors(
    store: RuntimeStore,
    *,
    start_date: str | None,
    as_of_date: str | None,
    fail_on_gap: bool,
) -> tuple[list[QualityIssue], dict]:
    required = {"date", "symbol", "field", "value"}
    dates: set[pd.Timestamp] = set()
    symbols: set[str] = set()
    field_dates: dict[str, set[pd.Timestamp]] = {}
    missing_columns: set[str] = set()
    duplicate_count = 0
    invalid_date_count = 0
    invalid_factor_count = 0
    value_count = 0
    for path in store.catalog.files("rq.daily_factors"):
        frame = pd.read_parquet(path)
        missing = required - set(frame)
        if missing:
            missing_columns.update(missing)
            continue
        normalized_dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        invalid_date_count += int(normalized_dates.isna().sum())
        dates.update(normalized_dates.dropna().tolist())
        symbols.update(frame["symbol"].dropna().astype(str).tolist())
        duplicate_count += int(frame.duplicated(["date", "symbol", "field"]).sum())
        values = pd.to_numeric(frame["value"], errors="coerce")
        invalid_factor_count += int((~np.isfinite(values)).sum())
        value_count += len(values)
        for field, indexes in frame.groupby("field").groups.items():
            available = normalized_dates.loc[indexes].dropna().tolist()
            field_dates.setdefault(str(field), set()).update(available)
    metrics = _set_coverage_metrics(dates, symbols)
    metrics["fields"] = sorted(field_dates)
    metrics["finite_value_rate"] = (
        float((value_count - invalid_factor_count) / value_count) if value_count else 0.0
    )
    metrics["field_distinct_dates"] = {
        field: len(values) for field, values in sorted(field_dates.items())
    }
    metrics["field_start_dates"] = {
        field: min(values).strftime("%Y-%m-%d")
        for field, values in sorted(field_dates.items())
        if values
    }
    metrics["field_end_dates"] = {
        field: max(values).strftime("%Y-%m-%d")
        for field, values in sorted(field_dates.items())
        if values
    }
    issues = _partition_issues(
        missing_columns=missing_columns,
        duplicate_count=duplicate_count,
        invalid_date_count=invalid_date_count,
    )
    if invalid_factor_count:
        issues.append(
            QualityIssue(
                "invalid_factors",
                f"Daily factors contain {invalid_factor_count} non-finite values",
            )
        )
    required_dates = _partitioned_bar_dates(
        store,
        start_date=start_date,
        as_of_date=as_of_date,
    )
    field_coverage = {
        field: float(len(required_dates & available) / len(required_dates))
        if required_dates
        else 0.0
        for field, available in sorted(field_dates.items())
    }
    metrics["field_date_coverage"] = field_coverage
    incomplete = {field: value for field, value in field_coverage.items() if value < 0.98}
    if fail_on_gap and incomplete:
        details = ", ".join(
            f"{field}={coverage:.2%}" for field, coverage in incomplete.items()
        )
        issues.append(
            QualityIssue(
                "factor_date_gap",
                f"Daily-factor trading-date coverage is incomplete: {details}",
            )
        )
    return issues, metrics


def _partitioned_bar_key_coverage(
    store: RuntimeStore,
    state_files: dict[tuple[str, str], Path],
    *,
    start_date: str | None,
    as_of_date: str | None,
) -> float:
    required_rows = 0
    matched_rows = 0
    for path in store.catalog.files("rq.bars"):
        bars = pd.read_parquet(path, columns=["date", "symbol"])
        bars["date"] = pd.to_datetime(bars["date"], errors="coerce").dt.normalize()
        bars = _bounded_dates(bars, start_date=start_date, as_of_date=as_of_date)
        bars = bars.dropna().drop_duplicates()
        if bars.empty:
            continue
        required_rows += len(bars)
        state_path = state_files.get(_partition_key(path))
        if state_path is None:
            continue
        state = pd.read_parquet(state_path, columns=["date", "symbol"])
        state["date"] = pd.to_datetime(state["date"], errors="coerce").dt.normalize()
        state = state.dropna().drop_duplicates()
        matched_rows += len(bars.merge(state, on=["date", "symbol"], how="inner"))
    return float(matched_rows / required_rows) if required_rows else 0.0


def _partitioned_bar_dates(
    store: RuntimeStore,
    *,
    start_date: str | None,
    as_of_date: str | None,
) -> set[pd.Timestamp]:
    dates: set[pd.Timestamp] = set()
    for path in store.catalog.files("rq.bars"):
        frame = pd.read_parquet(path, columns=["date"])
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        selected = _bounded_dates(frame, start_date=start_date, as_of_date=as_of_date)
        dates.update(selected["date"].dropna().tolist())
    return dates


def _partition_key(path: Path) -> tuple[str, str]:
    parts = {
        name: value
        for part in path.parts
        if "=" in part
        for name, value in [part.split("=", maxsplit=1)]
    }
    return parts.get("year", ""), parts.get("month", "")


def _set_coverage_metrics(dates: set[pd.Timestamp], symbols: set[str]) -> dict:
    return {
        "distinct_dates": len(dates),
        "start_date": min(dates).strftime("%Y-%m-%d") if dates else None,
        "end_date": max(dates).strftime("%Y-%m-%d") if dates else None,
        "symbol_count": len(symbols),
    }


def _partition_issues(
    *,
    missing_columns: set[str],
    duplicate_count: int,
    invalid_date_count: int,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if missing_columns:
        issues.append(
            QualityIssue("missing_columns", f"Missing columns: {sorted(missing_columns)}")
        )
    if duplicate_count:
        issues.append(
            QualityIssue("duplicate_keys", f"Dataset contains {duplicate_count} duplicate keys")
        )
    if invalid_date_count:
        issues.append(
            QualityIssue("invalid_dates", f"Dataset contains {invalid_date_count} invalid dates")
        )
    return issues


def _apply_bar_bounds(
    metrics: dict,
    issues: list[QualityIssue],
    *,
    start_date: str | None,
    as_of_date: str | None,
    fail_on_gap: bool,
) -> None:
    if start_date and metrics.get("start_date"):
        expected = pd.Timestamp(start_date).normalize()
        actual = pd.Timestamp(metrics["start_date"]).normalize()
        metrics["required_start_date"] = expected.strftime("%Y-%m-%d")
        if fail_on_gap and actual > expected:
            issues.append(
                QualityIssue(
                    "stale_start_date",
                    f"Dataset starts at {actual:%Y-%m-%d}, after required {expected:%Y-%m-%d}",
                )
            )
    if as_of_date and metrics.get("end_date"):
        expected = pd.Timestamp(as_of_date).normalize()
        actual = pd.Timestamp(metrics["end_date"]).normalize()
        metrics["as_of_date"] = expected.strftime("%Y-%m-%d")
        metrics["stale_days"] = int((expected - actual).days)
        if fail_on_gap and actual < expected:
            issues.append(
                QualityIssue(
                    "stale_end_date",
                    f"Dataset ends at {actual:%Y-%m-%d}, before required {expected:%Y-%m-%d}",
                )
            )


def _bounded_dates(
    frame: pd.DataFrame,
    *,
    start_date: str | None,
    as_of_date: str | None,
) -> pd.DataFrame:
    selected = frame
    if start_date:
        selected = selected.loc[selected["date"].ge(pd.Timestamp(start_date).normalize())]
    if as_of_date:
        selected = selected.loc[selected["date"].le(pd.Timestamp(as_of_date).normalize())]
    return selected


def _report(
    dataset: str,
    status: dict,
    issues: list[QualityIssue],
    metrics: dict,
) -> dict:
    return {
        "dataset": dataset,
        "status": "failed" if any(item.severity == "error" for item in issues) else "passed",
        "rows": status.get("rows", 0),
        "files": status.get("files", 0),
        "metrics": metrics,
        "issues": [asdict(item) for item in issues],
    }


__all__ = ["QualityIssue", "validate_all", "validate_dataset"]
