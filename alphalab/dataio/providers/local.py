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

from alphalab.dataio.catalog import DataCatalog
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
            out = self._resample(out, pd.offsets.MonthEnd())
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
    def _resample(df: pd.DataFrame, rule: str | pd.DateOffset) -> pd.DataFrame:
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


class LocalParquetInstrumentProvider:
    """Instrument metadata with listing intervals and optional dated snapshots."""

    def __init__(
        self,
        instrument_dir: str | Path,
        file_name: str = "instruments.parquet",
        *,
        bundled_market_path: str | Path | None = None,
    ):
        self.instrument_dir = Path(instrument_dir)
        self.path = self.instrument_dir / file_name
        self.bundled_market_path = (
            Path(bundled_market_path) if bundled_market_path is not None else None
        )
        self._cache: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = self._bundled_instrument_snapshot()
            return self._cache
        frame = pd.read_parquet(self.path).copy()
        if "symbol" not in frame:
            raise MissingDataError(f"Instrument file must include symbol: {self.path}")
        frame["symbol"] = frame["symbol"].astype(str).str.upper()
        for column in ("snapshot_date", "listed_date", "de_listed_date"):
            if column in frame:
                frame[column] = pd.to_datetime(frame[column], errors="coerce")
        self._cache = frame.dropna(subset=["symbol"]).reset_index(drop=True)
        return self._cache

    def _bundled_instrument_snapshot(self) -> pd.DataFrame:
        """Derive the explicit demo instrument contract from bundled bars.

        This capability is enabled only by ``create_default_engine``. Runtime
        profiles never receive a path here and therefore still require their
        provider-owned dated instrument snapshots.
        """

        columns = [
            "snapshot_date",
            "symbol",
            "listed_date",
            "de_listed_date",
            "asset_type",
        ]
        path = self.bundled_market_path
        if path is None or not path.exists():
            return pd.DataFrame(columns=columns)
        bars = pd.read_parquet(path, columns=["date", "symbol"])
        if bars.empty:
            return pd.DataFrame(columns=columns)
        bars["date"] = pd.to_datetime(bars["date"], errors="coerce")
        bars["symbol"] = bars["symbol"].astype(str).str.upper()
        bounds = (
            bars.dropna(subset=["date", "symbol"])
            .groupby("symbol", as_index=False)["date"]
            .agg(listed_date="min")
        )
        bounds["snapshot_date"] = bars["date"].min()
        bounds["de_listed_date"] = pd.NaT
        bounds["asset_type"] = "CS"
        return bounds[columns].reset_index(drop=True)

    def get_instruments(self, asof_date: Optional[str] = None) -> pd.DataFrame:
        frame = self._load()
        if frame.empty:
            return frame.copy()
        cutoff = pd.Timestamp(asof_date) if asof_date is not None else None
        selected = frame
        if "snapshot_date" in frame and frame["snapshot_date"].notna().any():
            snapshots = frame["snapshot_date"].dropna()
            eligible_snapshots = (
                snapshots.loc[snapshots.le(cutoff)] if cutoff is not None else snapshots
            )
            snapshot = eligible_snapshots.max() if not eligible_snapshots.empty else snapshots.min()
            selected = frame.loc[frame["snapshot_date"].eq(snapshot)].copy()
        if cutoff is not None:
            if "listed_date" in selected:
                selected = selected.loc[
                    selected["listed_date"].isna() | selected["listed_date"].le(cutoff)
                ]
            if "de_listed_date" in selected:
                selected = selected.loc[
                    selected["de_listed_date"].isna() | selected["de_listed_date"].gt(cutoff)
                ]
        return selected.drop_duplicates("symbol", keep="last").reset_index(drop=True)


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

    def __init__(
        self, factor_dir: str | Path | None = None, file_name: str = "factor_returns.parquet"
    ):
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
        return df.loc[
            (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end)), available
        ]

    def get_risk_free_rate(self, start: str, end: str, freq: str = "1M") -> pd.Series:
        df = self._load()
        if "rf" not in df:
            return pd.Series(dtype=float, name="rf")
        return df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end)), "rf"]


class PartitionedParquetMarketDataProvider(LocalParquetMarketDataProvider):
    """Market provider for ignored runtime partitions."""

    def __init__(self, runtime_root: str | Path):
        self.catalog = DataCatalog(runtime_root)
        self.market_dir = self.catalog.path("rq.bars")
        self.path = Path(runtime_root)
        self._cache: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache
        files = self.catalog.files("rq.bars")
        if not files:
            self._cache = pd.DataFrame(columns=["date", "symbol", "close"])
            return self._cache
        frame = pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["symbol"] = frame["symbol"].astype(str).str.upper()
        for dataset, field in (("rq.paused", "paused"), ("rq.is_st", "is_st")):
            state_files = self.catalog.files(dataset)
            if not state_files:
                continue
            state = pd.concat(
                (pd.read_parquet(path) for path in state_files), ignore_index=True
            )
            if not {"date", "symbol", field}.issubset(state.columns):
                continue
            state = state[["date", "symbol", field]].copy()
            state["date"] = pd.to_datetime(state["date"], errors="coerce")
            state["symbol"] = state["symbol"].astype(str).str.upper()
            state = state.drop_duplicates(["date", "symbol"], keep="last")
            frame = frame.merge(state, on=["date", "symbol"], how="left")
        if "paused" in frame:
            frame["is_suspended"] = frame["paused"].astype("boolean")
        self._cache = (
            frame.dropna(subset=["date", "symbol"])
            .drop_duplicates(["date", "symbol"], keep="last")
            .sort_values(["date", "symbol"])
            .reset_index(drop=True)
        )
        return self._cache


class PartitionedParquetInstrumentProvider(LocalParquetInstrumentProvider):
    """Instrument provider for ignored runtime snapshots."""

    def __init__(self, runtime_root: str | Path):
        self.catalog = DataCatalog(runtime_root)
        self.instrument_dir = self.catalog.path("rq.instruments")
        self.path = self.instrument_dir
        self._cache: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache
        files = self.catalog.files("rq.instruments")
        if not files:
            self._cache = pd.DataFrame(
                columns=["snapshot_date", "symbol", "listed_date", "de_listed_date"]
            )
            return self._cache
        frame = pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
        frame["symbol"] = frame["symbol"].astype(str).str.upper()
        for column in ("snapshot_date", "listed_date", "de_listed_date"):
            if column in frame:
                frame[column] = pd.to_datetime(frame[column], errors="coerce")
        self._cache = frame.dropna(subset=["symbol"]).reset_index(drop=True)
        return self._cache


class PartitionedParquetFundamentalProvider(LocalParquetFundamentalProvider):
    """Fundamental provider for canonical runtime partitions."""

    def __init__(self, runtime_root: str | Path):
        self.catalog = DataCatalog(runtime_root)
        self.fundamental_dir = self.catalog.path("canonical.fundamentals")
        self.path = self.fundamental_dir
        self._cache: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache
        files = self.catalog.files("canonical.fundamentals")
        if not files:
            self._cache = pd.DataFrame(columns=["quarter", "available_date", "symbol"])
            return self._cache
        frame = pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
        frame["available_date"] = pd.to_datetime(frame["available_date"], errors="coerce")
        frame["symbol"] = frame["symbol"].astype(str).str.upper()
        self._cache = (
            frame.dropna(subset=["quarter", "available_date", "symbol"])
            .drop_duplicates(["quarter", "symbol"], keep="last")
            .sort_values(["available_date", "quarter", "symbol"])
            .reset_index(drop=True)
        )
        return self._cache


class PartitionedParquetFactorProvider(LocalParquetFactorProvider):
    """Factor provider for derived runtime factor-return partitions."""

    def __init__(self, runtime_root: str | Path):
        self.catalog = DataCatalog(runtime_root)
        self.factor_dir = self.catalog.path("runtime.factor_returns")
        self.path = self.factor_dir
        self._cache: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache
        files = self.catalog.files("runtime.factor_returns")
        if not files:
            self._cache = pd.DataFrame()
            return self._cache
        frame = pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
        if "date" not in frame:
            raise MissingDataError("Runtime factor partitions must include date")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        self._cache = (
            frame.dropna(subset=["date"])
            .drop_duplicates(["date"], keep="last")
            .set_index("date")
            .sort_index()
        )
        return self._cache


class PartitionedParquetResearchDataProvider:
    """Historical state/factor/membership provider for the runtime store."""

    def __init__(self, runtime_root: str | Path):
        self.catalog = DataCatalog(runtime_root)
        self.path = Path(runtime_root)
        self._cache: dict[str, pd.DataFrame] = {}

    def _load(self, dataset: str) -> pd.DataFrame:
        if dataset in self._cache:
            return self._cache[dataset]
        files = self.catalog.files(dataset)
        frame = (
            pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
            if files
            else pd.DataFrame()
        )
        if "date" in frame:
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        if "symbol" in frame:
            frame["symbol"] = frame["symbol"].astype(str).str.upper()
        if "index_symbol" in frame:
            frame["index_symbol"] = frame["index_symbol"].astype(str).str.upper()
        self._cache[dataset] = frame
        return frame

    def get_market_state(
        self,
        symbols: list[str],
        start: str,
        end: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        requested = list(dict.fromkeys(fields or ["paused", "is_st"]))
        unknown = sorted(set(requested) - {"paused", "is_st", "is_suspended"})
        if unknown:
            raise KeyError(f"Unknown market-state fields: {unknown}")
        selected_symbols = set(_normal_symbols(symbols))
        output: pd.DataFrame | None = None
        for requested_field in requested:
            field = "paused" if requested_field == "is_suspended" else requested_field
            dataset = "rq.paused" if field == "paused" else "rq.is_st"
            frame = self._load(dataset)
            if frame.empty or not {"date", "symbol", field}.issubset(frame.columns):
                continue
            part = frame.loc[
                frame["symbol"].isin(selected_symbols)
                & frame["date"].between(pd.Timestamp(start), pd.Timestamp(end)),
                ["date", "symbol", field],
            ].copy()
            if requested_field == "is_suspended":
                part = part.rename(columns={"paused": "is_suspended"})
            output = part if output is None else output.merge(
                part, on=["date", "symbol"], how="outer"
            )
        return (
            output.sort_values(["date", "symbol"]).reset_index(drop=True)
            if output is not None
            else pd.DataFrame(columns=["date", "symbol", *requested])
        )

    def get_daily_factors(
        self,
        symbols: list[str],
        fields: list[str],
        start: str,
        end: str,
    ) -> pd.DataFrame:
        frame = self._load("rq.daily_factors")
        if frame.empty:
            return pd.DataFrame(columns=["date", "symbol", "field", "value"])
        selected_symbols = set(_normal_symbols(symbols))
        selected_fields = set(str(value) for value in fields)
        return (
            frame.loc[
                frame["symbol"].isin(selected_symbols)
                & frame["field"].astype(str).isin(selected_fields)
                & frame["date"].between(pd.Timestamp(start), pd.Timestamp(end))
            ]
            .drop_duplicates(["date", "symbol", "field"], keep="last")
            .sort_values(["date", "symbol", "field"])
            .reset_index(drop=True)
        )

    def get_index_components(
        self,
        index_symbols: list[str],
        start: str,
        end: str,
    ) -> pd.DataFrame:
        frame = self._load("rq.index_components")
        if frame.empty:
            return pd.DataFrame(columns=["date", "index_symbol", "symbol"])
        requested = set(_normal_symbols(index_symbols))
        return (
            frame.loc[
                frame["index_symbol"].isin(requested)
                & frame["date"].between(pd.Timestamp(start), pd.Timestamp(end))
            ]
            .drop_duplicates(["date", "index_symbol", "symbol"], keep="last")
            .sort_values(["date", "index_symbol", "symbol"])
            .reset_index(drop=True)
        )
