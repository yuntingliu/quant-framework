"""Focused RQ acquisition helpers used by the runtime synchronizer."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pandas as pd

from alphalab.dataio.errors import DataLoadError, DataValidationError
from alphalab.dataio.providers.rq import RQDataClient
from alphalab.dataio.rq_frames import (
    normalize_rq_bars,
    normalize_rq_daily_factor,
    normalize_rq_index_components,
    normalize_rq_instruments,
    normalize_rq_market_state,
    normalize_rq_suspension,
)
from alphalab.dataio.symbols import to_framework_symbol, to_rq_symbol

RQ_FACTOR_ALIASES = {
    "float_market_cap": "a_share_market_val_in_circulation",
    "market_cap": "market_cap",
    "roe": "return_on_equity",
}


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

    def instruments(
        self,
        snapshot_date: str,
        *,
        instrument_types: tuple[str, ...] = ("CS",),
        market: str = "cn",
    ) -> pd.DataFrame:
        rq = self.client.connect()
        frames: list[pd.DataFrame] = []
        for instrument_type in instrument_types:
            raw = self._retry(
                lambda instrument_type=instrument_type: rq.all_instruments(
                    type=instrument_type,
                    market=market,
                )
            )
            normalized = normalize_rq_instruments(
                raw,
                snapshot_date=snapshot_date,
                asset_type=instrument_type,
            )
            if not normalized.empty:
                frames.append(normalized)
        if not frames:
            raise DataLoadError(
                f"RQData returned no instruments for types {list(instrument_types)}"
            )
        return (
            pd.concat(frames, ignore_index=True)
            .dropna(subset=["symbol"])
            .drop_duplicates(["snapshot_date", "symbol"])
        )

    def daily_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        *,
        market: str = "cn",
        date_chunk_days: int | None = None,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> pd.DataFrame:
        frames = list(
            self.daily_bar_chunks(
                symbols,
                start,
                end,
                market=market,
                date_chunk_days=date_chunk_days,
                progress=progress,
                cancelled=cancelled,
            )
        )
        if not frames:
            raise DataLoadError("RQData returned no daily bars")
        return (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(["date", "symbol"], keep="last")
            .sort_values(["date", "symbol"])
            .reset_index(drop=True)
        )

    def daily_bar_chunks(
        self,
        symbols: list[str],
        start: str,
        end: str,
        *,
        market: str = "cn",
        date_chunk_days: int | None = 366,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[pd.DataFrame]:
        """Yield durable date-major bar chunks with strict adjusted/raw keys."""

        rq = self.client.connect()
        days = date_chunk_days or _calendar_days(start, end)
        for chunk_start, chunk_end in _date_chunks(start, end, days):
            parts: list[pd.DataFrame] = []
            for index, batch in enumerate(_chunks(symbols, self.stock_batch_size), start=1):
                if cancelled and cancelled():
                    return
                rq_batch = [to_rq_symbol(symbol) for symbol in batch]
                adjusted = self._retry(
                    lambda rq_batch=rq_batch, chunk_start=chunk_start, chunk_end=chunk_end: (
                        rq.get_price(
                            rq_batch,
                            start_date=chunk_start,
                            end_date=chunk_end,
                            frequency="1d",
                            fields=None,
                            adjust_type="pre",
                            expect_df=True,
                            market=market,
                        )
                    )
                )
                unadjusted = self._retry(
                    lambda rq_batch=rq_batch, chunk_start=chunk_start, chunk_end=chunk_end: (
                        rq.get_price(
                            rq_batch,
                            start_date=chunk_start,
                            end_date=chunk_end,
                            frequency="1d",
                            fields=["open", "high", "low", "close"],
                            adjust_type="none",
                            expect_df=True,
                            market=market,
                        )
                    )
                )
                normalized = normalize_rq_bars(adjusted, unadjusted)
                if not normalized.empty:
                    parts.append(normalized)
                if progress:
                    progress(f"bars {chunk_start}..{chunk_end} batch {index}")
            if parts:
                yield _combine_long(parts, ["date", "symbol"])

    def market_state_chunks(
        self,
        symbols: list[str],
        start: str,
        end: str,
        *,
        market: str = "cn",
        include_st: bool = True,
        date_chunk_days: int = 366,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[tuple[pd.DataFrame, pd.DataFrame]]:
        """Yield historical suspension and ST state for the same requested keys."""

        rq = self.client.connect()
        for chunk_start, chunk_end in _date_chunks(start, end, date_chunk_days):
            paused_parts: list[pd.DataFrame] = []
            st_parts: list[pd.DataFrame] = []
            for index, batch in enumerate(_chunks(symbols, self.stock_batch_size), start=1):
                if cancelled and cancelled():
                    return
                rq_batch = [to_rq_symbol(symbol) for symbol in batch]
                if include_st:
                    paused_raw = self._retry(
                        lambda rq_batch=rq_batch, chunk_start=chunk_start, chunk_end=chunk_end: (
                            rq.is_suspended(
                                rq_batch,
                                start_date=chunk_start,
                                end_date=chunk_end,
                                market=market,
                            )
                        )
                    )
                    paused = normalize_rq_market_state(paused_raw, field="paused")
                    st_raw = self._retry(
                        lambda rq_batch=rq_batch, chunk_start=chunk_start, chunk_end=chunk_end: (
                            rq.is_st_stock(
                                rq_batch,
                                start_date=chunk_start,
                                end_date=chunk_end,
                                market=market,
                            )
                        )
                    )
                    is_st = normalize_rq_market_state(st_raw, field="is_st")
                    _same_frame_keys(
                        paused,
                        is_st,
                        ["date", "symbol"],
                        label=f"paused/ST state {chunk_start}..{chunk_end}",
                    )
                else:
                    filled_raw = self._retry(
                        lambda rq_batch=rq_batch, chunk_start=chunk_start, chunk_end=chunk_end: (
                            rq.get_price(
                                rq_batch,
                                start_date=chunk_start,
                                end_date=chunk_end,
                                frequency="1d",
                                fields=["close"],
                                adjust_type="none",
                                skip_suspended=False,
                                expect_df=True,
                                market=market,
                            )
                        )
                    )
                    tradable_raw = self._retry(
                        lambda rq_batch=rq_batch, chunk_start=chunk_start, chunk_end=chunk_end: (
                            rq.get_price(
                                rq_batch,
                                start_date=chunk_start,
                                end_date=chunk_end,
                                frequency="1d",
                                fields=["close"],
                                adjust_type="none",
                                skip_suspended=True,
                                expect_df=True,
                                market=market,
                            )
                        )
                    )
                    paused = normalize_rq_suspension(filled_raw, tradable_raw)
                    is_st = pd.DataFrame(columns=["date", "symbol", "is_st"])
                if not paused.empty:
                    paused_parts.append(paused)
                    if include_st:
                        st_parts.append(is_st)
                if progress:
                    progress(f"market-state {chunk_start}..{chunk_end} batch {index}")
            if paused_parts:
                yield (
                    _combine_long(paused_parts, ["date", "symbol"]),
                    (
                        _combine_long(st_parts, ["date", "symbol"])
                        if include_st
                        else pd.DataFrame(columns=["date", "symbol", "is_st"])
                    ),
                )

    def daily_factor_chunks(
        self,
        symbols: list[str],
        fields: list[str],
        start: str,
        end: str,
        *,
        market: str = "cn",
        date_chunk_days: int = 366,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[pd.DataFrame]:
        """Yield canonical long daily factors in bounded date/symbol batches."""

        rq = self.client.connect()
        for chunk_start, chunk_end in _date_chunks(start, end, date_chunk_days):
            parts: list[pd.DataFrame] = []
            for field in fields:
                rq_field = RQ_FACTOR_ALIASES.get(field, field)
                for index, batch in enumerate(_chunks(symbols, self.stock_batch_size), start=1):
                    if cancelled and cancelled():
                        return
                    rq_batch = [to_rq_symbol(symbol) for symbol in batch]
                    raw = self._retry(
                        lambda rq_batch=rq_batch, rq_field=rq_field, chunk_start=chunk_start, chunk_end=chunk_end: (
                            rq.get_factor(
                                rq_batch,
                                rq_field,
                                start_date=chunk_start,
                                end_date=chunk_end,
                                market=market,
                            )
                        )
                    )
                    normalized = normalize_rq_daily_factor(raw, field=field)
                    if not normalized.empty:
                        parts.append(normalized)
                    if progress:
                        progress(f"daily-factor {field} {chunk_start}..{chunk_end} batch {index}")
            if parts:
                yield _combine_long(parts, ["date", "symbol", "field"])

    def index_components(
        self,
        index_symbols: Sequence[str],
        dates: Sequence[str],
        *,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> pd.DataFrame:
        """Fetch explicit historical index membership snapshots."""

        rq = self.client.connect()
        parts: list[pd.DataFrame] = []
        for index_symbol in index_symbols:
            for snapshot_date in dates:
                if cancelled and cancelled():
                    return _combine_long(parts, ["date", "index_symbol", "symbol"])
                raw = self._retry(
                    lambda index_symbol=index_symbol, snapshot_date=snapshot_date: (
                        rq.index_components(to_rq_symbol(index_symbol), date=snapshot_date)
                    )
                )
                normalized = normalize_rq_index_components(
                    raw,
                    index_symbol=index_symbol,
                    snapshot_date=snapshot_date,
                )
                if not normalized.empty:
                    parts.append(normalized)
                if progress:
                    progress(f"index-components {index_symbol} {snapshot_date}")
        output = _combine_long(parts, ["date", "index_symbol", "symbol"])
        if output.empty:
            raise DataLoadError("RQData returned no index components")
        return output

    def financials(
        self,
        symbols: list[str],
        fields: list[str],
        start_quarter: str,
        end_quarter: str,
        *,
        market: str = "cn",
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
                            market=market,
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

    def risk_free_curve(self, start: str, end: str, tenor: str = "1M") -> pd.DataFrame:
        """Fetch a China government yield tenor and normalize it to monthly return."""

        rq = self.client.connect()
        raw = self._retry(
            lambda: rq.get_yield_curve(
                start_date=start,
                end_date=end,
                tenor=tenor,
                market="cn",
            )
        )
        frame = _reset(pd.DataFrame(raw))
        if frame.empty:
            raise DataLoadError("RQData returned no risk-free yield curve")
        columns = _columns(frame)
        date_column = columns.get("date") or columns.get("trade_date") or columns.get("datetime")
        value_column = columns.get(tenor.lower()) or columns.get("yield")
        if date_column is None:
            first = frame.columns[0]
            if pd.api.types.is_datetime64_any_dtype(frame[first]):
                date_column = first
        if value_column is None:
            candidates = [column for column in frame.columns if column != date_column]
            if candidates:
                value_column = candidates[0]
        if date_column is None or value_column is None:
            raise DataValidationError("RQ yield curve requires date and tenor values")
        annual = pd.to_numeric(frame[value_column], errors="coerce")
        if annual.dropna().abs().median() > 1.0:
            annual = annual / 100.0
        monthly = (1.0 + annual.clip(lower=-0.999999)) ** (1.0 / 12.0) - 1.0
        output = pd.DataFrame(
            {
                "date": pd.to_datetime(frame[date_column], errors="coerce"),
                "rf": monthly,
            }
        )
        return output.dropna().drop_duplicates("date", keep="last").sort_values("date")

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


def _date_chunks(start: str, end: str, days: int) -> Iterator[tuple[str, str]]:
    if days < 1:
        raise ValueError("date chunk days must be positive")
    cursor = pd.Timestamp(start).normalize()
    final = pd.Timestamp(end).normalize()
    if cursor > final:
        raise ValueError("start must be on or before end")
    while cursor <= final:
        chunk_end = min(final, cursor + pd.Timedelta(days - 1, unit="D"))
        yield cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")
        cursor = chunk_end + pd.Timedelta(1, unit="D")


def _calendar_days(start: str, end: str) -> int:
    return int((pd.Timestamp(end).normalize() - pd.Timestamp(start).normalize()).days) + 1


def _combine_long(frames: list[pd.DataFrame], keys: list[str]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(keys, keep="last")
        .sort_values(keys)
        .reset_index(drop=True)
    )


def _same_frame_keys(
    left: pd.DataFrame,
    right: pd.DataFrame,
    keys: list[str],
    *,
    label: str,
) -> None:
    if any(key not in left or key not in right for key in keys):
        raise DataValidationError(f"RQData {label} response is missing key columns")
    mismatch = (
        left[keys]
        .drop_duplicates()
        .merge(right[keys].drop_duplicates(), on=keys, how="outer", indicator=True)
    )
    if mismatch["_merge"].ne("both").any():
        raise DataValidationError(
            f"RQData {label} key mismatch "
            f"({int(mismatch['_merge'].ne('both').sum())} unmatched rows)"
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


__all__ = ["RQAcquirer", "RQ_FACTOR_ALIASES", "quarter_range"]
