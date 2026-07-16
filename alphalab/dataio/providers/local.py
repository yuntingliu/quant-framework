"""Generic local parquet providers.

These providers are intentionally vendor-neutral. They read simple parquet files
that an adapter, notebook, or test fixture can create:

- market/bars.parquet: date, symbol, open, high, low, close, volume, amount?
- fundamentals/fundamentals.parquet: quarter, available_date, symbol, field columns
- factors/factor_returns.parquet: DatetimeIndex, one column per factor
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from alphalab.dataio.errors import MissingDataError
from alphalab.utils.paths import FACTOR_DIR, FUNDAMENTAL_DIR, MARKET_DIR


def _normal_symbols(symbols: list[str]) -> list[str]:
    return [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]


class LocalParquetMarketDataProvider:
    """Market data provider backed by one long-table parquet file."""

    def __init__(self, market_dir: str | Path | None = None, file_name: str = "bars.parquet"):
        self.market_dir = Path(market_dir) if market_dir is not None else MARKET_DIR
        self.path = self.market_dir / file_name
        self._cache: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = pd.DataFrame(columns=["date", "symbol", "close"])
            return self._cache
        df = pd.read_parquet(self.path)
        if "date" not in df or "symbol" not in df:
            raise MissingDataError(f"Market file must include date and symbol: {self.path}")
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df["symbol"] = df["symbol"].astype(str).str.upper()
        self._cache = df.sort_values(["date", "symbol"]).reset_index(drop=True)
        return self._cache

    def get_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        freq: str = "1d",
        fields: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        df = self._load()
        if df.empty:
            cols = ["date", "symbol"] + (fields or ["open", "high", "low", "close", "volume"])
            return pd.DataFrame(columns=list(dict.fromkeys(cols)))
        requested = set(_normal_symbols(symbols))
        mask = (
            df["symbol"].isin(requested)
            & (df["date"] >= pd.Timestamp(start))
            & (df["date"] <= pd.Timestamp(end))
        )
        out = df.loc[mask].copy()
        if fields:
            keep = ["date", "symbol"] + [field for field in fields if field in out.columns]
            out = out[keep]
        if freq in {"1w", "W"} and not out.empty:
            out = self._resample(out, "W")
        elif freq in {"1M", "M", "ME"} and not out.empty:
            out = self._resample(out, "ME")
        return out.sort_values(["date", "symbol"]).reset_index(drop=True)

    def get_symbols(self, universe: str = "all") -> list[str]:
        df = self._load()
        if df.empty:
            return []
        return sorted(df["symbol"].dropna().astype(str).str.upper().unique().tolist())

    def get_latest_date(self) -> str | None:
        df = self._load()
        if df.empty:
            return None
        return pd.Timestamp(df["date"].max()).strftime("%Y-%m-%d")

    @staticmethod
    def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
        agg = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "amount": "sum",
        }
        existing = {key: value for key, value in agg.items() if key in df.columns}
        return (
            df.set_index("date")
            .groupby("symbol", group_keys=False)
            .resample(rule)
            .agg(existing)
            .reset_index()
        )


class LocalParquetFundamentalProvider:
    """Fundamental provider backed by one long-table parquet file."""

    def __init__(
        self,
        fundamental_dir: str | Path | None = None,
        file_name: str = "fundamentals.parquet",
    ):
        self.fundamental_dir = Path(fundamental_dir) if fundamental_dir else FUNDAMENTAL_DIR
        self.path = self.fundamental_dir / file_name
        self._cache: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = pd.DataFrame(columns=["quarter", "available_date", "symbol"])
            return self._cache
        df = pd.read_parquet(self.path)
        if "quarter" not in df or "symbol" not in df:
            raise MissingDataError(f"Fundamental file must include quarter and symbol: {self.path}")
        df = df.copy()
        df["symbol"] = df["symbol"].astype(str).str.upper()
        if "available_date" in df:
            df["available_date"] = pd.to_datetime(df["available_date"], errors="coerce")
        self._cache = df.sort_values(["quarter", "symbol"]).reset_index(drop=True)
        return self._cache

    def get_fundamentals(
        self,
        symbols: list[str],
        fields: list[str],
        start_quarter: str,
        end_quarter: str,
        asof_date: Optional[str] = None,
        strict: bool = True,
    ) -> pd.DataFrame:
        df = self._load()
        requested = set(_normal_symbols(symbols))
        keep_fields = [field for field in fields if field in df.columns]
        missing = sorted(set(fields) - set(keep_fields))
        if missing and strict:
            raise MissingDataError(f"Missing fundamental fields: {missing}")
        metadata = ["quarter", "symbol"]
        if "available_date" in df:
            metadata.insert(1, "available_date")
        if df.empty:
            return pd.DataFrame(columns=metadata + keep_fields)
        mask = (
            df["symbol"].isin(requested)
            & (df["quarter"].astype(str) >= start_quarter)
            & (df["quarter"].astype(str) <= end_quarter)
        )
        if asof_date is not None and "available_date" in df:
            mask &= df["available_date"].notna() & df["available_date"].le(pd.Timestamp(asof_date))
        out = df.loc[mask, metadata + keep_fields].copy()
        sort_columns = ["quarter", "symbol"]
        if "available_date" in out:
            sort_columns = ["available_date", "quarter", "symbol"]
        return out.sort_values(sort_columns).reset_index(drop=True)


class LocalParquetFactorProvider:
    """Factor-return provider backed by one parquet file."""

    def __init__(self, factor_dir: str | Path | None = None, file_name: str = "factor_returns.parquet"):
        self.factor_dir = Path(factor_dir) if factor_dir else FACTOR_DIR
        self.path = self.factor_dir / file_name
        self._cache: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = pd.DataFrame()
            return self._cache
        df = pd.read_parquet(self.path)
        df.index = pd.to_datetime(df.index)
        self._cache = df.sort_index()
        return self._cache

    def get_factors(
        self,
        names: list[str],
        start: str,
        end: str,
        freq: str = "1M",
        strict: bool = True,
    ) -> pd.DataFrame:
        df = self._load()
        available = [name for name in names if name in df.columns]
        missing = sorted(set(names) - set(available))
        if missing and strict:
            raise MissingDataError(f"Missing factor returns: {missing}")
        if df.empty:
            return pd.DataFrame(columns=available)
        return df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end)), available]

    def get_risk_free_rate(self, start: str, end: str, freq: str = "1M") -> pd.Series:
        df = self._load()
        if "rf" not in df:
            return pd.Series(dtype=float, name="rf")
        return df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end)), "rf"]
