"""Streaming quality gates for canonical runtime datasets."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
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
    symbols: Iterable[str] | None = None,
) -> dict:
    """Validate one dataset without loading large daily histories at once."""

    if dataset not in DATASETS:
        raise KeyError(f"Unknown runtime dataset: {dataset}")
    store = RuntimeStore(root)
    selected_symbols = (
        {str(value).strip().upper() for value in symbols if str(value).strip()}
        if symbols is not None
        else None
    )
    status = store.catalog.status(dataset)
    if status["status"] != "ready":
        report = _report(
            dataset,
            status,
            [QualityIssue(status["status"], status.get("error") or "Dataset is not ready")],
            {},
        )
        store.operations.record_quality(dataset, report)
        return report

    if dataset == "rq.bars":
        issues, metrics = _validate_bars(
            store,
            start_date=start_date,
            as_of_date=as_of_date,
            fail_on_gap=fail_on_gap,
            symbols=selected_symbols,
        )
    elif dataset in {"rq.paused", "rq.is_st"}:
        issues, metrics = _validate_market_state(
            store,
            dataset,
            start_date=start_date,
            as_of_date=as_of_date,
            fail_on_gap=fail_on_gap,
            symbols=selected_symbols,
        )
    elif dataset == "rq.daily_factors":
        issues, metrics = _validate_daily_factors(
            store,
            start_date=start_date,
            as_of_date=as_of_date,
            fail_on_gap=fail_on_gap,
            symbols=selected_symbols,
        )
    else:
        issues, metrics = _validate_compact(
            store,
            dataset,
            start_date=start_date,
            as_of_date=as_of_date,
            fail_on_gap=fail_on_gap,
        )
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
    symbols: Iterable[str] | None = None,
) -> list[dict]:
    selected_symbols = tuple(symbols) if symbols is not None else None
    selected = (
        list(dict.fromkeys(str(value).strip() for value in datasets if str(value).strip()))
        if datasets is not None
        else [dataset for dataset, spec in DATASETS.items() if spec.configured]
    )
    if not selected:
        raise ValueError("At least one dataset is required")
    unknown = sorted(set(selected) - set(DATASETS))
    if unknown:
        raise KeyError(f"Unknown runtime datasets: {unknown}")
    return [
        validate_dataset(
            dataset,
            root,
            start_date=start_date,
            as_of_date=as_of_date,
            fail_on_gap=fail_on_gap,
            symbols=selected_symbols,
        )
        for dataset in selected
    ]


def _validate_bars(
    store: RuntimeStore,
    *,
    start_date: str | None,
    as_of_date: str | None,
    fail_on_gap: bool,
    symbols: set[str] | None,
) -> tuple[list[QualityIssue], dict]:
    required = {
        "date", "symbol", "open", "high", "low", "close", "raw_close", "volume", "amount"
    }
    dates: set[pd.Timestamp] = set()
    observed_symbols: set[str] = set()
    missing_columns: set[str] = set()
    duplicate_count = invalid_date_count = 0
    invalid_price_count = invalid_activity_count = invalid_ohlc_count = 0
    for frame in _partitions(store, "rq.bars"):
        frame = _filter_symbols(frame, symbols)
        if frame.empty:
            continue
        missing = required - set(frame)
        if missing:
            missing_columns.update(missing)
            continue
        normalized_dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        invalid_date_count += int(normalized_dates.isna().sum())
        dates.update(normalized_dates.dropna().tolist())
        observed_symbols.update(frame["symbol"].dropna().astype(str).str.upper())
        duplicate_count += int(frame.duplicated(["date", "symbol"]).sum())
        numeric = frame[list(required - {"date", "symbol"})].apply(pd.to_numeric, errors="coerce")
        prices = numeric[["open", "high", "low", "close", "raw_close"]]
        invalid_price_count += int(
            (prices.isna() | ~np.isfinite(prices) | prices.le(0)).any(axis=1).sum()
        )
        activity = numeric[["volume", "amount"]]
        invalid_activity_count += int(
            (activity.isna() | ~np.isfinite(activity) | activity.lt(0)).any(axis=1).sum()
        )
        invalid_ohlc_count += int(
            (
                numeric["high"].lt(numeric["low"])
                | numeric["high"].lt(numeric[["open", "close"]].max(axis=1))
                | numeric["low"].gt(numeric[["open", "close"]].min(axis=1))
            ).sum()
        )
    issues = _daily_issues(
        missing_columns=missing_columns,
        duplicate_count=duplicate_count,
        invalid_date_count=invalid_date_count,
    )
    for code, count, label in (
        ("invalid_prices", invalid_price_count, "price"),
        ("invalid_activity", invalid_activity_count, "volume/amount"),
        ("invalid_ohlc", invalid_ohlc_count, "OHLC"),
    ):
        if count:
            issues.append(QualityIssue(code, f"{count} invalid {label} rows"))
    metrics = _coverage_metrics(dates, observed_symbols)
    metrics.update(
        {
            "duplicate_rows": duplicate_count,
            "invalid_dates": invalid_date_count,
            "invalid_prices": invalid_price_count,
            "invalid_activity": invalid_activity_count,
            "invalid_ohlc": invalid_ohlc_count,
        }
    )
    _check_bounds(metrics, issues, start_date, as_of_date, fail_on_gap)
    return issues, metrics


def _validate_market_state(
    store: RuntimeStore,
    dataset: str,
    *,
    start_date: str | None,
    as_of_date: str | None,
    fail_on_gap: bool,
    symbols: set[str] | None,
) -> tuple[list[QualityIssue], dict]:
    field = "paused" if dataset == "rq.paused" else "is_st"
    required = {"date", "symbol", field}
    dates: set[pd.Timestamp] = set()
    observed_symbols: set[str] = set()
    missing_columns: set[str] = set()
    duplicate_count = invalid_date_count = invalid_value_count = 0
    state_keys: set[tuple[pd.Timestamp, str]] = set()
    for frame in _partitions(store, dataset):
        frame = _filter_symbols(frame, symbols)
        if frame.empty:
            continue
        missing = required - set(frame)
        if missing:
            missing_columns.update(missing)
            continue
        normalized_dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        normalized_symbols = frame["symbol"].astype(str).str.upper()
        invalid_date_count += int(normalized_dates.isna().sum())
        dates.update(normalized_dates.dropna().tolist())
        observed_symbols.update(normalized_symbols.dropna())
        duplicate_count += int(frame.duplicated(["date", "symbol"]).sum())
        invalid_value_count += int(_coerce_boolean(frame[field]).isna().sum())
        state_keys.update(
            (date, symbol)
            for date, symbol in zip(normalized_dates, normalized_symbols, strict=False)
            if pd.notna(date) and symbol
        )
    issues = _daily_issues(
        missing_columns=missing_columns,
        duplicate_count=duplicate_count,
        invalid_date_count=invalid_date_count,
    )
    if invalid_value_count:
        issues.append(QualityIssue("invalid_state", f"{invalid_value_count} invalid {field} rows"))
    required_keys = _bar_keys(
        store,
        start_date=start_date,
        as_of_date=as_of_date,
        symbols=symbols,
    )
    coverage = float(len(required_keys & state_keys) / len(required_keys)) if required_keys else 0.0
    if fail_on_gap and coverage < 0.98:
        issues.append(
            QualityIssue("bar_key_gap", f"{field} covers only {coverage:.2%} of required RQ bar keys")
        )
    metrics = _coverage_metrics(dates, observed_symbols)
    metrics.update(
        {
            "bar_key_coverage": coverage,
            "required_bar_keys": len(required_keys),
            "duplicate_rows": duplicate_count,
            "invalid_dates": invalid_date_count,
            "invalid_values": invalid_value_count,
        }
    )
    _check_bounds(metrics, issues, start_date, as_of_date, fail_on_gap)
    return issues, metrics


def _validate_daily_factors(
    store: RuntimeStore,
    *,
    start_date: str | None,
    as_of_date: str | None,
    fail_on_gap: bool,
    symbols: set[str] | None,
) -> tuple[list[QualityIssue], dict]:
    required = {"date", "symbol", "field", "value"}
    dates: set[pd.Timestamp] = set()
    observed_symbols: set[str] = set()
    field_dates: dict[str, set[pd.Timestamp]] = {}
    missing_columns: set[str] = set()
    duplicate_count = invalid_date_count = invalid_value_count = 0
    for frame in _partitions(store, "rq.daily_factors"):
        frame = _filter_symbols(frame, symbols)
        if frame.empty:
            continue
        missing = required - set(frame)
        if missing:
            missing_columns.update(missing)
            continue
        normalized_dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        invalid_date_count += int(normalized_dates.isna().sum())
        dates.update(normalized_dates.dropna().tolist())
        observed_symbols.update(frame["symbol"].dropna().astype(str).str.upper())
        duplicate_count += int(frame.duplicated(["date", "symbol", "field"]).sum())
        values = pd.to_numeric(frame["value"], errors="coerce")
        invalid_value_count += int((values.isna() | ~np.isfinite(values)).sum())
        for field, field_frame in frame.assign(__date=normalized_dates).groupby("field"):
            field_dates.setdefault(str(field), set()).update(field_frame["__date"].dropna())
    issues = _daily_issues(
        missing_columns=missing_columns,
        duplicate_count=duplicate_count,
        invalid_date_count=invalid_date_count,
    )
    if invalid_value_count:
        issues.append(
            QualityIssue("invalid_factor_values", f"{invalid_value_count} invalid factor values")
        )
    required_dates = _bar_dates(
        store,
        start_date=start_date,
        as_of_date=as_of_date,
        symbols=symbols,
    )
    coverage = {
        field: float(len(values & required_dates) / len(required_dates)) if required_dates else 0.0
        for field, values in sorted(field_dates.items())
    }
    incomplete = {field: value for field, value in coverage.items() if value < 0.98}
    if fail_on_gap and incomplete:
        details = ", ".join(f"{field}={value:.2%}" for field, value in incomplete.items())
        issues.append(
            QualityIssue("factor_date_gap", f"Daily factor date coverage is incomplete: {details}")
        )
    metrics = _coverage_metrics(dates, observed_symbols)
    metrics.update(
        {
            "fields": sorted(field_dates),
            "field_date_coverage": coverage,
            "field_start_dates": {
                field: min(values).strftime("%Y-%m-%d")
                for field, values in sorted(field_dates.items())
                if values
            },
            "field_end_dates": {
                field: max(values).strftime("%Y-%m-%d")
                for field, values in sorted(field_dates.items())
                if values
            },
            "duplicate_rows": duplicate_count,
            "invalid_dates": invalid_date_count,
            "invalid_values": invalid_value_count,
        }
    )
    _check_bounds(metrics, issues, start_date, as_of_date, fail_on_gap)
    return issues, metrics


def _validate_compact(
    store: RuntimeStore,
    dataset: str,
    *,
    start_date: str | None,
    as_of_date: str | None,
    fail_on_gap: bool,
) -> tuple[list[QualityIssue], dict]:
    frame = store.read(dataset)
    spec = DATASETS[dataset]
    issues: list[QualityIssue] = []
    missing = sorted(set(spec.key_columns) - set(frame))
    if missing:
        issues.append(QualityIssue("missing_columns", f"Missing key columns: {missing}"))
    elif frame.duplicated(list(spec.key_columns)).any():
        issues.append(QualityIssue("duplicate_keys", "Dataset contains duplicate primary keys"))
    dates = (
        set(pd.to_datetime(frame[spec.date_column], errors="coerce").dropna().dt.normalize())
        if spec.date_column and spec.date_column in frame
        else set()
    )
    symbols = set(frame["symbol"].dropna().astype(str).str.upper()) if "symbol" in frame else set()
    metrics = _coverage_metrics(dates, symbols)

    if dataset == "rq.index_components":
        required = {"date", "index_symbol", "symbol"}
        missing_components = sorted(required - set(frame))
        if missing_components:
            issues.append(
                QualityIssue("missing_columns", f"Missing index-component columns: {missing_components}")
            )
        else:
            dated = frame.assign(date=pd.to_datetime(frame["date"], errors="coerce"))
            ranges = dated.groupby("index_symbol")["date"].agg(["min", "max"])
            metrics["indexes"] = sorted(ranges.index.astype(str))
            metrics["index_start_dates"] = _dated_mapping(ranges["min"])
            metrics["index_end_dates"] = _dated_mapping(ranges["max"])
            if fail_on_gap and as_of_date:
                expected_month = pd.Timestamp(as_of_date).to_period("M")
                stale = [
                    str(name)
                    for name, value in ranges["max"].items()
                    if pd.isna(value) or pd.Timestamp(value).to_period("M") < expected_month
                ]
                if stale:
                    issues.append(
                        QualityIssue(
                            "stale_index_end_dates",
                            "Index snapshots do not reach the required month: "
                            + ", ".join(sorted(stale)),
                        )
                    )
    elif dataset.startswith("rq.financials."):
        required = {"quarter", "symbol", "info_date", "if_adjusted"}
        missing_financials = sorted(required - set(frame))
        if missing_financials:
            issues.append(QualityIssue("missing_columns", f"Missing PIT fields: {missing_financials}"))
        elif pd.to_datetime(frame["info_date"], errors="coerce").isna().any():
            issues.append(QualityIssue("invalid_disclosure_date", "PIT rows contain invalid info_date"))
    elif dataset == "canonical.fundamentals":
        required = {
            "quarter", "available_date", "symbol", "ep", "bp", "roe",
            "gross_margin", "leverage", "profit_growth", "revenue_growth",
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
            issues.append(QualityIssue("missing_columns", f"Missing factor columns: {missing_factors}"))
        else:
            values = frame[list(required - {"date"})].apply(pd.to_numeric, errors="coerce")
            if values[["MKT", "rf"]].isna().any().any():
                issues.append(QualityIssue("invalid_factor_returns", "MKT and rf must be finite"))
    _check_bounds(metrics, issues, start_date, as_of_date, fail_on_gap)
    return issues, metrics


def _partitions(store: RuntimeStore, dataset: str) -> Iterator[pd.DataFrame]:
    for path in store.catalog.files(dataset):
        yield pd.read_parquet(path)


def _bar_keys(
    store: RuntimeStore,
    *,
    start_date: str | None,
    as_of_date: str | None,
    symbols: set[str] | None,
) -> set[tuple[pd.Timestamp, str]]:
    keys: set[tuple[pd.Timestamp, str]] = set()
    for path in store.catalog.files("rq.bars"):
        frame = pd.read_parquet(path, columns=["date", "symbol"])
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        frame = _bounded(frame, start_date, as_of_date)
        frame = _filter_symbols(frame, symbols)
        keys.update(
            (date, str(symbol).upper())
            for date, symbol in frame[["date", "symbol"]].dropna().itertuples(index=False)
        )
    return keys


def _bar_dates(
    store: RuntimeStore,
    *,
    start_date: str | None,
    as_of_date: str | None,
    symbols: set[str] | None,
) -> set[pd.Timestamp]:
    return {
        date
        for date, _symbol in _bar_keys(
            store,
            start_date=start_date,
            as_of_date=as_of_date,
            symbols=symbols,
        )
    }


def _filter_symbols(frame: pd.DataFrame, symbols: set[str] | None) -> pd.DataFrame:
    if symbols is None or "symbol" not in frame:
        return frame
    return frame.loc[frame["symbol"].astype(str).str.upper().isin(symbols)]


def _coerce_boolean(values: pd.Series) -> pd.Series:
    accepted = {
        True: True,
        False: False,
        "true": True,
        "false": False,
        "1": True,
        "0": False,
    }
    return values.map(
        lambda value: accepted.get(value.strip().lower() if isinstance(value, str) else value, pd.NA)
    ).astype("boolean")


def _bounded(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    selected = frame
    if start:
        selected = selected.loc[selected["date"].ge(pd.Timestamp(start).normalize())]
    if end:
        selected = selected.loc[selected["date"].le(pd.Timestamp(end).normalize())]
    return selected


def _daily_issues(
    *, missing_columns: set[str], duplicate_count: int, invalid_date_count: int
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if missing_columns:
        issues.append(QualityIssue("missing_columns", f"Missing columns: {sorted(missing_columns)}"))
    if duplicate_count:
        issues.append(QualityIssue("duplicate_keys", f"Dataset contains {duplicate_count} duplicate keys"))
    if invalid_date_count:
        issues.append(QualityIssue("invalid_dates", f"Dataset contains {invalid_date_count} invalid dates"))
    return issues


def _coverage_metrics(dates: set[pd.Timestamp], symbols: set[str]) -> dict:
    return {
        "distinct_dates": len(dates),
        "start_date": min(dates).strftime("%Y-%m-%d") if dates else None,
        "end_date": max(dates).strftime("%Y-%m-%d") if dates else None,
        "symbol_count": len(symbols),
    }


def _check_bounds(
    metrics: dict,
    issues: list[QualityIssue],
    start_date: str | None,
    as_of_date: str | None,
    fail_on_gap: bool,
) -> None:
    tolerance = pd.Timedelta(days=7)
    if start_date and metrics.get("start_date"):
        expected = pd.Timestamp(start_date).normalize()
        actual = pd.Timestamp(metrics["start_date"]).normalize()
        metrics["required_start_date"] = expected.strftime("%Y-%m-%d")
        if fail_on_gap and actual > expected + tolerance:
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
        if fail_on_gap and actual < expected - tolerance:
            issues.append(
                QualityIssue(
                    "stale_end_date",
                    f"Dataset ends at {actual:%Y-%m-%d}, before required {expected:%Y-%m-%d}",
                )
            )


def _dated_mapping(values: pd.Series) -> dict[str, str]:
    return {
        str(name): pd.Timestamp(value).strftime("%Y-%m-%d")
        for name, value in values.items()
        if pd.notna(value)
    }


def _report(dataset: str, status: dict, issues: list[QualityIssue], metrics: dict) -> dict:
    return {
        "dataset": dataset,
        "status": "failed" if any(item.severity == "error" for item in issues) else "passed",
        "rows": status.get("rows", 0),
        "files": status.get("files", 0),
        "metrics": metrics,
        "issues": [asdict(item) for item in issues],
    }


__all__ = ["QualityIssue", "validate_all", "validate_dataset"]
