"""Canonical frame adapters for raw results returned by public rqdatac calls."""

from __future__ import annotations

from typing import Any, Iterable

import pandas as pd

from alphalab.dataio.errors import DataValidationError
from alphalab.dataio.symbols import to_framework_symbol, to_rq_symbol

_REQUIRED_BAR_FIELDS = ("open", "high", "low", "close", "volume", "amount")


def rq_order_book_ids(symbols: Iterable[str]) -> list[str]:
    """Convert framework or RQ symbols into rqdatac order-book IDs."""

    return list(dict.fromkeys(to_rq_symbol(str(symbol)) for symbol in symbols))


def framework_symbols(symbols: Iterable[str]) -> list[str]:
    """Convert framework or RQ symbols into canonical framework symbols."""

    return list(dict.fromkeys(to_framework_symbol(to_rq_symbol(str(symbol))) for symbol in symbols))


def normalize_rq_instruments(
    raw: Any,
    *,
    snapshot_date: str,
    asset_type: str,
) -> pd.DataFrame:
    """Normalize one ``rq.all_instruments`` result for runtime publication."""

    frame = _reset(pd.DataFrame(raw))
    if frame.empty:
        return pd.DataFrame()
    columns = _columns(frame)
    symbol_column = columns.get("order_book_id") or columns.get("symbol")
    if symbol_column is None:
        raise DataValidationError("RQ instruments do not include order_book_id")
    output = pd.DataFrame(
        {
            "snapshot_date": pd.Timestamp(snapshot_date).normalize(),
            "symbol": frame[symbol_column].map(to_framework_symbol),
            "asset_type": str(asset_type).upper(),
            "name": _series(frame, columns, "symbol", "display_name", "name"),
            "listed_date": pd.to_datetime(
                _series(frame, columns, "listed_date", "listed_at"),
                errors="coerce",
            ),
            "de_listed_date": pd.to_datetime(
                _series(frame, columns, "de_listed_date", "de_listed_at"),
                errors="coerce",
            ),
            "status": _series(frame, columns, "status"),
            "is_st": _series(frame, columns, "is_st"),
            "industry": _series(
                frame,
                columns,
                "industry",
                "industry_name",
                "sector_code",
            ),
        }
    )
    for source_column in frame.columns:
        target = str(source_column).strip().lower()
        if source_column == symbol_column or not target or target in output.columns:
            continue
        output[target] = frame[source_column].to_numpy()
    return output.dropna(subset=["symbol"]).drop_duplicates(["snapshot_date", "symbol"])


def normalize_rq_bars(adjusted_raw: Any, raw_close: Any) -> pd.DataFrame:
    """Combine adjusted and unadjusted ``rq.get_price`` results."""

    adjusted = _normalize_discovered_bars(adjusted_raw)
    unadjusted = _normalize_selected_bars(raw_close, ("close",)).rename(
        columns={"close": "raw_close"}
    )
    if adjusted.empty or unadjusted.empty:
        return pd.DataFrame()
    return (
        adjusted.merge(unadjusted, on=["date", "symbol"], how="inner")
        .drop_duplicates(["date", "symbol"], keep="last")
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )


def normalize_rq_financials(raw: Any, fields: Iterable[str]) -> pd.DataFrame:
    """Normalize one ``rq.get_pit_financials_ex`` result."""

    requested = tuple(dict.fromkeys(str(field) for field in fields))
    frame = _reset(pd.DataFrame(raw))
    if frame.empty:
        return pd.DataFrame()
    columns = _columns(frame)
    symbol_column = columns.get("order_book_id") or columns.get("symbol")
    quarter_column = columns.get("quarter")
    info_column = columns.get("info_date") or columns.get("announce_date")
    if symbol_column is None or quarter_column is None or info_column is None:
        raise DataValidationError("RQ PIT financials require order_book_id, quarter, and info_date")
    output = pd.DataFrame(
        {
            "symbol": frame[symbol_column].map(to_framework_symbol),
            "quarter": frame[quarter_column].astype(str).str.lower(),
            "info_date": pd.to_datetime(frame[info_column], errors="coerce"),
            "if_adjusted": pd.to_numeric(
                (
                    frame[columns["if_adjusted"]]
                    if "if_adjusted" in columns
                    else pd.Series(0, index=frame.index)
                ),
                errors="coerce",
            )
            .fillna(0)
            .astype(int),
        }
    )
    for field in requested:
        column = columns.get(field.lower())
        output[field] = (
            pd.to_numeric(frame[column], errors="coerce")
            if column is not None
            else pd.Series(float("nan"), index=frame.index)
        )
    return output.dropna(subset=["symbol", "quarter", "info_date"])


def normalize_rq_yield_curve(raw: Any, *, tenor: str = "1M") -> pd.DataFrame:
    """Normalize ``rq.get_yield_curve`` into monthly risk-free returns."""

    frame = _reset(pd.DataFrame(raw))
    if frame.empty:
        return pd.DataFrame()
    columns = _columns(frame)
    date_column = columns.get("date") or columns.get("trade_date") or columns.get("datetime")
    value_column = columns.get(tenor.lower()) or columns.get("yield")
    if date_column is None and len(frame.columns):
        first = frame.columns[0]
        if pd.api.types.is_datetime64_any_dtype(frame[first]):
            date_column = first
    if value_column is None:
        value_column = next(
            (column for column in frame.columns if column != date_column),
            None,
        )
    if date_column is None or value_column is None:
        raise DataValidationError("RQ yield curve requires date and tenor values")
    annual = pd.to_numeric(frame[value_column], errors="coerce")
    if not annual.dropna().empty and annual.dropna().abs().median() > 1.0:
        annual = annual / 100.0
    monthly = (1.0 + annual.clip(lower=-0.999999)) ** (1.0 / 12.0) - 1.0
    return (
        pd.DataFrame(
            {
                "date": pd.to_datetime(frame[date_column], errors="coerce"),
                "rf": monthly,
            }
        )
        .dropna()
        .drop_duplicates("date", keep="last")
        .sort_values("date")
    )


def _normalize_discovered_bars(raw: Any) -> pd.DataFrame:
    frame = _reset(pd.DataFrame(raw))
    if frame.empty:
        return pd.DataFrame()
    columns = _columns(frame)
    symbol_column = columns.get("order_book_id") or columns.get("symbol")
    date_column = columns.get("datetime") or columns.get("date") or columns.get("trading_date")
    if symbol_column is None or date_column is None:
        raise DataValidationError("RQData bars must include order_book_id and date")
    output = pd.DataFrame(
        {
            "date": pd.to_datetime(frame[date_column], errors="coerce"),
            "symbol": frame[symbol_column].map(to_framework_symbol),
        }
    )
    for source_column in frame.columns:
        if source_column in {symbol_column, date_column}:
            continue
        source_name = str(source_column).strip().lower()
        target = "amount" if source_name == "total_turnover" else source_name
        if not target or target in output:
            continue
        values = frame[source_column]
        numeric = pd.to_numeric(values, errors="coerce")
        output[target] = (
            numeric
            if values.dropna().empty or numeric.notna().sum() >= values.notna().sum()
            else values
        )
    if "volume" in output:
        output["volume"] = pd.to_numeric(output["volume"], errors="coerce") / 100.0
    missing = sorted(set(_REQUIRED_BAR_FIELDS) - set(output))
    if missing:
        raise DataValidationError(f"RQData daily bars do not include required fields: {missing}")
    return output.dropna(subset=["date", "symbol"]).reset_index(drop=True)


def _normalize_selected_bars(raw: Any, fields: Iterable[str]) -> pd.DataFrame:
    frame = _reset(pd.DataFrame(raw))
    if frame.empty:
        return pd.DataFrame()
    columns = _columns(frame)
    symbol_column = columns.get("order_book_id") or columns.get("symbol")
    date_column = columns.get("datetime") or columns.get("date") or columns.get("trading_date")
    if symbol_column is None or date_column is None:
        raise DataValidationError("RQData bars must include order_book_id and date")
    output = pd.DataFrame(
        {
            "date": pd.to_datetime(frame[date_column], errors="coerce"),
            "symbol": frame[symbol_column].map(to_framework_symbol),
        }
    )
    for field in fields:
        source = "total_turnover" if field == "amount" else field
        column = columns.get(source)
        output[field] = (
            pd.to_numeric(frame[column], errors="coerce")
            if column is not None
            else pd.Series(float("nan"), index=frame.index)
        )
    return output.dropna(subset=["date", "symbol"]).reset_index(drop=True)


def _reset(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.reset_index() if isinstance(frame.index, pd.MultiIndex) or frame.index.name else frame
    )


def _columns(frame: pd.DataFrame) -> dict[str, Any]:
    return {str(column).lower(): column for column in frame.columns}


def _series(frame: pd.DataFrame, columns: dict[str, Any], *names: str) -> pd.Series:
    for name in names:
        if name in columns:
            return frame[columns[name]]
    return pd.Series(pd.NA, index=frame.index, dtype="object")


__all__ = [
    "framework_symbols",
    "normalize_rq_bars",
    "normalize_rq_financials",
    "normalize_rq_instruments",
    "normalize_rq_yield_curve",
    "rq_order_book_ids",
]
