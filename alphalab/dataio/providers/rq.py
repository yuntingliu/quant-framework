"""Optional RQData providers configured only through project environment values."""
from __future__ import annotations

import importlib
import os
import threading
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Mapping, Optional

import pandas as pd

from alphalab.dataio.errors import DataLoadError, DataValidationError, MissingDataError
from alphalab.utils.env import load_env_files

_BAR_FIELDS = ("open", "high", "low", "close", "volume", "amount")
_MONTHLY_FREQUENCIES = {"1M", "M", "ME"}
_ENV_KEYS = ("RQ_USER", "RQ_PASSWORD", "RQ_HOST")


@dataclass(frozen=True)
class RQDataConfig:
    """The complete, minimal configuration required by the RQData client."""

    user: str = field(repr=False)
    password: str = field(repr=False)
    host: str = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RQDataConfig":
        if env is None:
            load_env_files()
            env = os.environ
        values = {key: str(env.get(key, "")).strip() for key in _ENV_KEYS}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise DataLoadError(
                "RQData is not configured. Set these values in the local .env: "
                + ", ".join(missing)
            )
        return cls(
            user=values["RQ_USER"],
            password=values["RQ_PASSWORD"],
            host=values["RQ_HOST"],
        )


class RQDataClient:
    """Lazy RQData client that never connects during package import."""

    def __init__(self, config: RQDataConfig, module: ModuleType | Any | None = None):
        self.config = config
        self._module = module
        self._connected = False
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "RQDataClient":
        return cls(RQDataConfig.from_env())

    def connect(self) -> Any:
        if self._connected:
            return self._module
        with self._lock:
            if self._connected:
                return self._module
            if self._module is None:
                try:
                    self._module = importlib.import_module("rqdatac")
                except ImportError as exc:
                    raise DataLoadError(
                        'RQData support is not installed. Run: pip install -e ".[rq]"'
                    ) from exc
            try:
                self._module.init(
                    self.config.user,
                    self.config.password,
                    self.config.host,
                    lazy=False,
                )
            except Exception as exc:
                raise DataLoadError(
                    "RQData initialization failed. Verify RQ_USER, RQ_PASSWORD, and RQ_HOST."
                ) from exc
            self._connected = True
        return self._module

    @property
    def connected(self) -> bool:
        return self._connected


class RQDataProvider:
    """Market and PIT-fundamental provider backed by the RQData Python client."""

    def __init__(
        self,
        client: RQDataClient,
        *,
        adjust_type: str = "pre",
        fundamental_batch_size: int = 200,
    ):
        if fundamental_batch_size <= 0:
            raise ValueError("fundamental_batch_size must be positive")
        self.client = client
        self.adjust_type = adjust_type
        self.fundamental_batch_size = fundamental_batch_size

    @classmethod
    def from_env(cls, **kwargs: Any) -> "RQDataProvider":
        return cls(RQDataClient.from_env(), **kwargs)

    def get_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        freq: str = "1d",
        fields: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        requested = list(dict.fromkeys(fields or _BAR_FIELDS))
        unknown = sorted(set(requested) - set(_BAR_FIELDS))
        if unknown:
            raise DataValidationError(f"Unsupported RQ market fields: {unknown}")
        if not symbols:
            return _empty_bars(requested)

        rq_fields = ["total_turnover" if name == "amount" else name for name in requested]
        request_frequency = "1d" if freq in _MONTHLY_FREQUENCIES else freq
        rq = self.client.connect()
        try:
            raw = rq.get_price(
                [_to_rq_symbol(symbol) for symbol in symbols],
                start_date=start,
                end_date=end,
                frequency=request_frequency,
                fields=rq_fields,
                adjust_type=self.adjust_type,
                expect_df=True,
            )
        except Exception as exc:
            raise DataLoadError("RQData market request failed") from exc

        out = _normalize_bars(raw, requested)
        if freq in _MONTHLY_FREQUENCIES and not out.empty:
            out = _resample_bars(out, "ME")
        return out

    def get_symbols(self, universe: str = "all") -> list[str]:
        if universe.lower() not in {"all", "stock", "stocks", "cs"}:
            raise MissingDataError(f"RQData universe is not supported: {universe!r}")
        rq = self.client.connect()
        try:
            raw = rq.all_instruments(type="CS", market="cn")
        except Exception as exc:
            raise DataLoadError("RQData instrument request failed") from exc
        frame = _reset_index(pd.DataFrame(raw))
        if frame.empty:
            return []
        columns = _columns(frame)
        symbol_column = columns.get("order_book_id") or columns.get("symbol")
        if symbol_column is None:
            raise DataValidationError("RQData instruments do not include order_book_id")
        return sorted({_from_rq_symbol(value) for value in frame[symbol_column].dropna()})

    def get_fundamentals(
        self,
        symbols: list[str],
        fields: list[str],
        start_quarter: str,
        end_quarter: str,
        asof_date: Optional[str] = None,
        strict: bool = True,
    ) -> pd.DataFrame:
        requested = list(dict.fromkeys(fields))
        if not requested:
            raise ValueError("fields must not be empty")
        if not symbols:
            return _empty_fundamentals(requested)

        rq = self.client.connect()
        frames: list[pd.DataFrame] = []
        rq_symbols = [_to_rq_symbol(symbol) for symbol in symbols]
        for offset in range(0, len(rq_symbols), self.fundamental_batch_size):
            batch = rq_symbols[offset : offset + self.fundamental_batch_size]
            try:
                raw = rq.get_pit_financials_ex(
                    order_book_ids=batch,
                    fields=requested,
                    start_quarter=start_quarter,
                    end_quarter=end_quarter,
                    date=asof_date,
                    statements="all",
                    market="cn",
                )
            except Exception as exc:
                raise DataLoadError("RQData fundamental request failed") from exc
            normalized = _normalize_fundamentals(
                raw,
                requested,
                asof_date=asof_date,
                strict=strict,
            )
            if not normalized.empty:
                frames.append(normalized)

        if not frames:
            return _empty_fundamentals(requested)
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.sort_values(
            ["symbol", "quarter", "if_adjusted", "available_date"]
        ).drop_duplicates(["quarter", "symbol"], keep="first")
        return combined.sort_values(
            ["available_date", "quarter", "symbol"]
        ).reset_index(drop=True)


def _normalize_bars(raw: Any, fields: list[str]) -> pd.DataFrame:
    frame = _reset_index(pd.DataFrame(raw))
    if frame.empty:
        return _empty_bars(fields)
    columns = _columns(frame)
    symbol_column = columns.get("order_book_id") or columns.get("symbol")
    date_column = columns.get("datetime") or columns.get("date") or columns.get("trading_date")
    if symbol_column is None or date_column is None:
        raise DataValidationError("RQData bars must include order_book_id and date")

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(frame[date_column], errors="coerce"),
            "symbol": frame[symbol_column].map(_from_rq_symbol),
        }
    )
    for name in fields:
        source_name = "total_turnover" if name == "amount" else name
        source_column = columns.get(source_name)
        if source_column is None:
            out[name] = pd.NA
        else:
            out[name] = pd.to_numeric(frame[source_column], errors="coerce")
    if "volume" in out:
        out["volume"] = out["volume"] / 100.0
    return (
        out.dropna(subset=["date", "symbol"])
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )


def _normalize_fundamentals(
    raw: Any,
    fields: list[str],
    *,
    asof_date: str | None,
    strict: bool,
) -> pd.DataFrame:
    frame = _reset_index(pd.DataFrame(raw))
    if frame.empty:
        return _empty_fundamentals(fields)
    columns = _columns(frame)
    symbol_column = columns.get("order_book_id") or columns.get("symbol")
    quarter_column = columns.get("quarter")
    date_column = (
        columns.get("available_date")
        or columns.get("info_date")
        or columns.get("announce_date")
    )
    if symbol_column is None or quarter_column is None:
        raise DataValidationError("RQData fundamentals must include order_book_id and quarter")
    if date_column is None and strict:
        raise DataValidationError("RQData fundamentals do not include a disclosure date")

    available = [name for name in fields if name.lower() in columns]
    missing = sorted(set(fields) - set(available))
    if missing and strict:
        raise MissingDataError(f"RQData did not return fundamental fields: {missing}")

    out = pd.DataFrame(
        {
            "quarter": frame[quarter_column].astype(str),
            "available_date": (
                pd.to_datetime(frame[date_column], errors="coerce")
                if date_column is not None
                else pd.NaT
            ),
            "symbol": frame[symbol_column].map(_from_rq_symbol),
            "if_adjusted": (
                pd.to_numeric(frame[columns["if_adjusted"]], errors="coerce").fillna(0)
                if "if_adjusted" in columns
                else 0
            ),
        }
    )
    for name in available:
        out[name] = frame[columns[name.lower()]]
    if asof_date is not None:
        cutoff = pd.Timestamp(asof_date)
        out = out.loc[out["available_date"].notna() & out["available_date"].le(cutoff)]
    return out.dropna(subset=["quarter", "symbol"]).reset_index(drop=True)


def _resample_bars(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    aggregations = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "amount": "sum",
    }
    existing = {name: method for name, method in aggregations.items() if name in frame}
    return (
        frame.set_index("date")
        .groupby("symbol", group_keys=False)
        .resample(rule)
        .agg(existing)
        .reset_index()
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )


def _empty_bars(fields: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "symbol", *fields])


def _empty_fundamentals(fields: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        columns=["quarter", "available_date", "symbol", "if_adjusted", *fields]
    )


def _reset_index(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.index, pd.MultiIndex) or frame.index.name is not None:
        return frame.reset_index()
    return frame


def _columns(frame: pd.DataFrame) -> dict[str, Any]:
    return {str(column).lower(): column for column in frame.columns}


def _to_rq_symbol(symbol: str) -> str:
    value = str(symbol).strip().upper()
    suffixes = {
        ".SH": ".XSHG",
        ".SZ": ".XSHE",
        ".BJ": ".XBEI",
    }
    for suffix, rq_suffix in suffixes.items():
        if value.endswith(suffix):
            return value[: -len(suffix)] + rq_suffix
    if value.endswith((".XSHG", ".XSHE", ".XBEI")):
        return value
    if value.isdigit() and len(value) == 6:
        if value.startswith(("4", "8", "92")):
            return value + ".XBEI"
        if value.startswith(("5", "6", "9")):
            return value + ".XSHG"
        return value + ".XSHE"
    return value


def _from_rq_symbol(symbol: Any) -> str:
    if pd.isna(symbol):
        return ""
    value = str(symbol).strip().upper()
    suffixes = {
        ".XSHG": ".SH",
        ".XSHE": ".SZ",
        ".XBEI": ".BJ",
    }
    for suffix, framework_suffix in suffixes.items():
        if value.endswith(suffix):
            return value[: -len(suffix)] + framework_suffix
    return value


__all__ = ["RQDataClient", "RQDataConfig", "RQDataProvider"]
