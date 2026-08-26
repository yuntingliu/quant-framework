"""Focused RQ acquisition helpers used by the runtime synchronizer."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any

import pandas as pd

from alphalab.dataio.errors import DataLoadError, DataValidationError
from alphalab.dataio.providers.rq import RQDataClient, RQDataProvider
from alphalab.dataio.symbols import is_a_share_symbol, to_framework_symbol, to_rq_symbol

RQ_FACTOR_ALIASES = {
    "float_market_cap": "a_share_market_val_in_circulation",
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

    def instruments(self, _snapshot_date: str) -> pd.DataFrame:
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
                # RQ returns a current reference snapshot when date is omitted.
                "snapshot_date": pd.Timestamp.now().normalize(),
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
        output = output.dropna(subset=["symbol"])
        output = output.loc[output["symbol"].map(is_a_share_symbol)]
        if output.empty:
            raise DataLoadError("RQData returned no valid A-share instruments")
        return output.drop_duplicates(["snapshot_date", "symbol"])

    def daily_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        *,
        date_chunk_days: int | None = None,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> pd.DataFrame:
        frames = list(
            self.daily_bar_chunks(
                symbols,
                start,
                end,
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
        date_chunk_days: int | None = 366,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[pd.DataFrame]:
        """Yield complete date-major bar chunks for recoverable local writes."""

        if not symbols:
            raise ValueError("symbols must not be empty")
        adjusted_provider = RQDataProvider(self.client, adjust_type="pre")
        raw_provider = RQDataProvider(self.client, adjust_type="none")
        chunk_days = int(date_chunk_days or _calendar_days(start, end))
        for chunk_start, chunk_end in _date_chunks(start, end, chunk_days):
            frames: list[pd.DataFrame] = []
            for index, batch in enumerate(_chunks(symbols, self.stock_batch_size), start=1):
                if cancelled and cancelled():
                    return
                adjusted = self._retry(
                    lambda batch=list(batch): adjusted_provider.get_bars(
                        batch,
                        chunk_start,
                        chunk_end,
                    )
                )
                raw = self._retry(
                    lambda batch=list(batch): raw_provider.get_bars(
                        batch,
                        chunk_start,
                        chunk_end,
                        fields=["close"],
                    )
                ).rename(columns={"close": "raw_close"})
                _require_same_keys(
                    adjusted,
                    raw,
                    ["date", "symbol"],
                    label=f"adjusted/raw bars {chunk_start}..{chunk_end}",
                )
                combined = adjusted.merge(raw, on=["date", "symbol"], how="inner")
                if not combined.empty:
                    frames.append(combined)
                if progress:
                    progress(f"bars {chunk_start}..{chunk_end} batch {index}")
            if frames:
                yield (
                    pd.concat(frames, ignore_index=True)
                    .drop_duplicates(["date", "symbol"], keep="last")
                    .sort_values(["date", "symbol"])
                    .reset_index(drop=True)
                )

    def market_state_chunks(
        self,
        symbols: list[str],
        start: str,
        end: str,
        *,
        date_chunk_days: int = 366,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[tuple[pd.DataFrame, pd.DataFrame]]:
        """Yield historical suspension and ST state as long date/symbol tables."""

        rq = self.client.connect()
        rq_symbols = [to_rq_symbol(symbol) for symbol in symbols]
        for chunk_start, chunk_end in _date_chunks(start, end, date_chunk_days):
            paused_parts: list[pd.DataFrame] = []
            st_parts: list[pd.DataFrame] = []
            for index, batch in enumerate(_chunks(rq_symbols, self.stock_batch_size), start=1):
                if cancelled and cancelled():
                    return
                paused_raw = self._retry(
                    lambda batch=list(batch): rq.is_suspended(
                        batch,
                        start_date=chunk_start,
                        end_date=chunk_end,
                    )
                )
                st_raw = self._retry(
                    lambda batch=list(batch): rq.is_st_stock(
                        batch,
                        start_date=chunk_start,
                        end_date=chunk_end,
                    )
                )
                paused = _normalize_bool_state(paused_raw, "paused")
                is_st = _normalize_bool_state(st_raw, "is_st")
                _require_same_keys(
                    paused,
                    is_st,
                    ["date", "symbol"],
                    label=f"paused/ST state {chunk_start}..{chunk_end}",
                )
                if not paused.empty:
                    paused_parts.append(paused)
                if not is_st.empty:
                    st_parts.append(is_st)
                if progress:
                    progress(f"market-state {chunk_start}..{chunk_end} batch {index}")
            if paused_parts and st_parts:
                yield (
                    _combine_long(paused_parts, ["date", "symbol"]),
                    _combine_long(st_parts, ["date", "symbol"]),
                )
            elif paused_parts or st_parts:
                raise DataLoadError(
                    f"RQData returned only one market-state table for {chunk_start}..{chunk_end}"
                )

    def daily_factor_chunks(
        self,
        symbols: list[str],
        fields: Iterable[str],
        start: str,
        end: str,
        *,
        date_chunk_days: int = 366,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[pd.DataFrame]:
        """Yield normalized long-form daily-factor chunks."""

        rq = self.client.connect()
        rq_symbols = [to_rq_symbol(symbol) for symbol in symbols]
        stored_fields = list(dict.fromkeys(str(field).strip() for field in fields if str(field).strip()))
        if not stored_fields:
            raise ValueError("daily factor fields must not be empty")
        for chunk_start, chunk_end in _date_chunks(start, end, date_chunk_days):
            parts: list[pd.DataFrame] = []
            for field in stored_fields:
                rq_field = RQ_FACTOR_ALIASES.get(field, field)
                field_rows = 0
                for index, batch in enumerate(_chunks(rq_symbols, self.stock_batch_size), start=1):
                    if cancelled and cancelled():
                        return
                    raw = self._retry(
                        lambda batch=list(batch), rq_field=rq_field: rq.get_factor(
                            order_book_ids=batch,
                            factor=rq_field,
                            start_date=chunk_start,
                            end_date=chunk_end,
                        )
                    )
                    normalized = _normalize_daily_factor(raw, field)
                    if not normalized.empty:
                        parts.append(normalized)
                        field_rows += len(normalized)
                    if progress:
                        progress(
                            f"daily-factor {field} {chunk_start}..{chunk_end} batch {index}"
                        )
                if field_rows == 0:
                    raise DataLoadError(
                        f"RQData factor {field!r} returned no rows for {chunk_start}..{chunk_end}"
                    )
            if parts:
                yield _combine_long(parts, ["date", "symbol", "field"])

    def index_components(
        self,
        index_symbols: Iterable[str],
        dates: Iterable[str],
        *,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> pd.DataFrame:
        rq = self.client.connect()
        rows: list[dict[str, object]] = []
        for index_symbol in index_symbols:
            rq_index = to_rq_symbol(index_symbol)
            framework_index = to_framework_symbol(rq_index)
            for value in dates:
                if cancelled and cancelled():
                    break
                date_text = pd.Timestamp(value).strftime("%Y-%m-%d")
                raw = self._retry(lambda: rq.index_components(rq_index, date=date_text))
                for member in _component_values(raw):
                    rows.append(
                        {
                            "date": pd.Timestamp(date_text).normalize(),
                            "index_symbol": framework_index,
                            "symbol": to_framework_symbol(member),
                        }
                    )
                if progress:
                    progress(f"index-components {framework_index} {date_text}")
        if not rows:
            raise DataLoadError("RQData returned no index components")
        return _combine_long(
            [pd.DataFrame(rows)],
            ["date", "index_symbol", "symbol"],
        )

    @staticmethod
    def symbols_for_period(
        instruments: pd.DataFrame,
        start: str,
        end: str,
    ) -> list[str]:
        if instruments is None or instruments.empty:
            return []
        frame = instruments.copy()
        listed = pd.to_datetime(frame.get("listed_date"), errors="coerce")
        delisted = pd.to_datetime(frame.get("de_listed_date"), errors="coerce")
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        active = (listed.isna() | listed.le(end_ts)) & (delisted.isna() | delisted.ge(start_ts))
        return sorted(frame.loc[active, "symbol"].dropna().astype(str).unique().tolist())

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
        date_column = (
            columns.get("date")
            or columns.get("trade_date")
            or columns.get("datetime")
        )
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


def _date_chunks(start: str, end: str, days: int) -> Iterator[tuple[str, str]]:
    if days <= 0:
        raise ValueError("date chunk days must be positive")
    cursor = pd.Timestamp(start).normalize()
    last = pd.Timestamp(end).normalize()
    if cursor > last:
        raise ValueError("start must be on or before end")
    while cursor <= last:
        chunk_end = min(last, cursor + pd.Timedelta(days=days - 1))
        yield cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")
        cursor = chunk_end + pd.Timedelta(days=1)


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


def _require_same_keys(
    left: pd.DataFrame,
    right: pd.DataFrame,
    keys: list[str],
    *,
    label: str,
) -> None:
    if any(key not in left or key not in right for key in keys):
        raise DataValidationError(f"RQData {label} response is missing key columns")
    left_keys = left[keys].drop_duplicates()
    right_keys = right[keys].drop_duplicates()
    mismatch = left_keys.merge(right_keys, on=keys, how="outer", indicator=True)
    mismatch = mismatch.loc[mismatch["_merge"].ne("both")]
    if not mismatch.empty:
        raise DataValidationError(
            f"RQData {label} key mismatch ({len(mismatch)} unmatched rows)"
        )


def _normalize_bool_state(raw: Any, field: str) -> pd.DataFrame:
    frame = pd.DataFrame(raw)
    if frame.empty:
        return pd.DataFrame(columns=["date", "symbol", field])
    if isinstance(frame.index, pd.MultiIndex):
        series = frame.iloc[:, 0] if frame.shape[1] == 1 else frame.stack()
        frame = series.unstack(level=0)
    try:
        frame.index = pd.to_datetime(frame.index)
    except Exception as exc:
        raise DataValidationError(f"RQData {field} response has no date index") from exc
    frame.index.name = "date"
    long = frame.reset_index().melt(id_vars="date", var_name="symbol", value_name=field)
    long["date"] = pd.to_datetime(long["date"], errors="coerce").dt.normalize()
    long["symbol"] = long["symbol"].map(to_framework_symbol)
    long[field] = long[field].astype("boolean")
    return long.dropna(subset=["date", "symbol", field]).reset_index(drop=True)


def _normalize_daily_factor(raw: Any, field: str) -> pd.DataFrame:
    if raw is None:
        return pd.DataFrame(columns=["date", "symbol", "field", "value"])
    frame = pd.DataFrame(raw)
    if frame.empty:
        return pd.DataFrame(columns=["date", "symbol", "field", "value"])
    rows: list[dict[str, object]] = []
    if isinstance(frame.index, pd.MultiIndex):
        series = frame.iloc[:, 0] if frame.shape[1] == 1 else frame.stack()
        for index, value in series.dropna().items():
            parts = index if isinstance(index, tuple) else (index,)
            date_value = next((part for part in parts if _looks_like_date(part)), None)
            symbol = next(
                (part for part in parts if isinstance(part, str) and "." in part),
                None,
            )
            if date_value is not None and symbol is not None:
                rows.append(_factor_row(date_value, symbol, field, value))
        return pd.DataFrame(rows, columns=["date", "symbol", "field", "value"])
    sample = frame.index[: min(3, len(frame.index))]
    if len(sample) and all(_looks_like_date(value) for value in sample):
        for symbol in frame.columns:
            for date_value, value in frame[symbol].dropna().items():
                rows.append(_factor_row(date_value, str(symbol), field, value))
        return pd.DataFrame(rows, columns=["date", "symbol", "field", "value"])
    reset = _reset(frame)
    columns = _columns(reset)
    date_column = columns.get("date") or columns.get("datetime") or columns.get("trading_date")
    symbol_column = columns.get("order_book_id") or columns.get("symbol")
    value_column = columns.get(field.lower()) or columns.get("value")
    if value_column is None:
        candidates = [
            column for column in reset.columns if column not in {date_column, symbol_column}
        ]
        value_column = candidates[-1] if candidates else None
    if date_column is None or symbol_column is None or value_column is None:
        raise DataValidationError(f"RQData factor {field!r} response shape is unsupported")
    for row in reset[[date_column, symbol_column, value_column]].itertuples(index=False):
        rows.append(_factor_row(row[0], str(row[1]), field, row[2]))
    return pd.DataFrame(rows, columns=["date", "symbol", "field", "value"])


def _factor_row(date_value: object, symbol: str, field: str, value: object) -> dict[str, object]:
    return {
        "date": pd.Timestamp(date_value).normalize(),
        "symbol": to_framework_symbol(symbol),
        "field": field,
        "value": pd.to_numeric(value, errors="coerce"),
    }


def _looks_like_date(value: object) -> bool:
    try:
        pd.Timestamp(value)
        return True
    except Exception:
        return False


def _component_values(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, pd.DataFrame):
        if raw.empty:
            return []
        column = "order_book_id" if "order_book_id" in raw else raw.columns[0]
        return raw[column].dropna().astype(str).tolist()
    if isinstance(raw, pd.Series):
        return raw.dropna().astype(str).tolist()
    return [str(value) for value in raw if value is not None]


__all__ = ["RQAcquirer", "RQ_FACTOR_ALIASES", "quarter_range"]
