"""Focused RQ acquisition helpers used by the runtime synchronizer."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pandas as pd

from alphalab.dataio.errors import DataLoadError, DataValidationError
from alphalab.dataio.providers.rq import RQDataClient, RQDataProvider
from alphalab.dataio.symbols import to_framework_symbol, to_rq_symbol


def quarter_range(start: str, end: str) -> list[str]:
    start_year, start_number = _quarter_tuple(start)
    end_year, end_number = _quarter_tuple(end)
    if (start_year, start_number) > (end_year, end_number):
        raise ValueError("start_quarter must be on or before end_quarter")
    values: list[str] = []
    year, number = start_year, start_number
    while (year, number) <= (end_year, end_number):
        values.append(f"{year}q{number}")
        number += 1
        if number == 5:
            year += 1
            number = 1
    return values


def _quarter_tuple(value: str) -> tuple[int, int]:
    normalized = str(value).strip().lower()
    if len(normalized) != 6 or normalized[4] != "q":
        raise ValueError(f"Invalid quarter: {value}")
    year, quarter = int(normalized[:4]), int(normalized[5])
    if quarter not in {1, 2, 3, 4}:
        raise ValueError(f"Invalid quarter: {value}")
    return year, quarter


def _chunks(values: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


class RQAcquirer:
    """Network acquisition with bounded batches and transient retries."""

    def __init__(
        self,
        client: RQDataClient,
        *,
        stock_batch_size: int = 200,
        quarter_batch_size: int = 50,
        retries: int = 3,
        retry_delay: float = 0.25,
    ):
        self.client = client
        self.stock_batch_size = min(200, max(1, stock_batch_size))
        self.quarter_batch_size = min(50, max(1, quarter_batch_size))
        self.retries = max(1, retries)
        self.retry_delay = max(0.0, retry_delay)

    @classmethod
    def from_env(cls, **kwargs: Any) -> "RQAcquirer":
        return cls(RQDataClient.from_env(), **kwargs)

    def instruments(self, snapshot_date: str) -> pd.DataFrame:
        rq = self.client.connect()
        raw = self._retry(lambda: rq.all_instruments(type="CS", market="cn"))
        frame = _reset(pd.DataFrame(raw))
        if frame.empty:
            raise DataLoadError("RQData returned no A-share instruments")
        columns = _columns(frame)
        symbol_column = columns.get("order_book_id") or columns.get("symbol")
        if symbol_column is None:
            raise DataValidationError("RQ instruments do not include order_book_id")
        output = pd.DataFrame(
            {
                "snapshot_date": pd.Timestamp(snapshot_date).normalize(),
                "symbol": frame[symbol_column].map(to_framework_symbol),
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
                "is_st": pd.Series(pd.NA, index=frame.index, dtype="boolean"),
                "industry": pd.Series(pd.NA, index=frame.index, dtype="string"),
            }
        )
        return output.dropna(subset=["symbol"]).drop_duplicates(["snapshot_date", "symbol"])

    def daily_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        *,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for index, batch in enumerate(_chunks(symbols, self.stock_batch_size), start=1):
            if cancelled and cancelled():
                break
            adjusted_provider = RQDataProvider(self.client, adjust_type="pre")
            raw_provider = RQDataProvider(self.client, adjust_type="none")
            adjusted = self._retry(
                lambda batch=list(batch): adjusted_provider.get_bars(batch, start, end)
            )
            raw = self._retry(
                lambda batch=list(batch): raw_provider.get_bars(
                    batch,
                    start,
                    end,
                    fields=["close"],
                )
            ).rename(columns={"close": "raw_close"})
            combined = adjusted.merge(raw, on=["date", "symbol"], how="inner")
            if not combined.empty:
                frames.append(combined)
            if progress:
                progress(f"bars batch {index}")
        if not frames:
            raise DataLoadError("RQData returned no daily bars")
        return (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(["date", "symbol"], keep="last")
            .sort_values(["date", "symbol"])
            .reset_index(drop=True)
        )

    def financials(
        self,
        symbols: list[str],
        fields: list[str],
        start_quarter: str,
        end_quarter: str,
        *,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> pd.DataFrame:
        rq = self.client.connect()
        rq_symbols = [to_rq_symbol(symbol) for symbol in symbols]
        quarters = quarter_range(start_quarter, end_quarter)
        quarter_batches = list(_chunks(quarters, self.quarter_batch_size))
        frames: list[pd.DataFrame] = []
        batch_number = 0
        for quarter_batch in quarter_batches:
            for stock_batch in _chunks(rq_symbols, self.stock_batch_size):
                if cancelled and cancelled():
                    return _combine_financials(frames)
                batch_number += 1
                raw = self._retry(
                    lambda stock_batch=list(stock_batch), quarter_batch=list(quarter_batch): (
                        rq.get_pit_financials_ex(
                            order_book_ids=stock_batch,
                            fields=fields,
                            start_quarter=quarter_batch[0],
                            end_quarter=quarter_batch[-1],
                            statements="all",
                            market="cn",
                        )
                    )
                )
                normalized = _normalize_financials(raw, fields)
                if not normalized.empty:
                    frames.append(normalized)
                if progress:
                    progress(f"financial batch {batch_number}")
        output = _combine_financials(frames)
        if output.empty:
            raise DataLoadError("RQData returned no PIT financial statements")
        return output

    def _retry(self, call: Callable[[], Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                return call()
            except DataValidationError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.retry_delay * (2**attempt))
        raise DataLoadError("RQData request failed after retries") from last_error


def _normalize_financials(raw: Any, fields: list[str]) -> pd.DataFrame:
    frame = _reset(pd.DataFrame(raw))
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "quarter", "info_date", "if_adjusted", *fields])
    columns = _columns(frame)
    symbol_column = columns.get("order_book_id") or columns.get("symbol")
    quarter_column = columns.get("quarter")
    info_column = columns.get("info_date") or columns.get("announce_date")
    if symbol_column is None or quarter_column is None or info_column is None:
        raise DataValidationError(
            "RQ PIT financials require order_book_id, quarter, and info_date"
        )
    output = pd.DataFrame(
        {
            "symbol": frame[symbol_column].map(to_framework_symbol),
            "quarter": frame[quarter_column].astype(str).str.lower(),
            "info_date": pd.to_datetime(frame[info_column], errors="coerce"),
            "if_adjusted": pd.to_numeric(
                frame[columns["if_adjusted"]]
                if "if_adjusted" in columns
                else pd.Series(0, index=frame.index),
                errors="coerce",
            ).fillna(0).astype(int),
        }
    )
    for field in fields:
        column = columns.get(field.lower())
        output[field] = (
            pd.to_numeric(frame[column], errors="coerce")
            if column is not None
            else pd.Series(float("nan"), index=frame.index)
        )
    return output.dropna(subset=["symbol", "quarter", "info_date"])


def _combine_financials(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(["symbol", "quarter", "info_date", "if_adjusted"], keep="last")
        .sort_values(["symbol", "quarter", "if_adjusted", "info_date"])
        .reset_index(drop=True)
    )


def _reset(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.index, pd.MultiIndex) or frame.index.name is not None:
        return frame.reset_index()
    return frame


def _columns(frame: pd.DataFrame) -> dict[str, Any]:
    return {str(column).lower(): column for column in frame.columns}


def _series(frame: pd.DataFrame, columns: dict[str, Any], *names: str) -> pd.Series:
    for name in names:
        if name in columns:
            return frame[columns[name]]
    return pd.Series(pd.NA, index=frame.index, dtype="object")


__all__ = ["RQAcquirer", "quarter_range"]
