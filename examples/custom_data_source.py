"""Minimal custom Data SDK source backed by user-owned CSV files.

Expected bars columns: date,symbol,open,high,low,close,volume,amount
Expected instruments columns: symbol,snapshot_date,listed_date,de_listed_date,asset_type
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from alphalab.data_sdk.v1 import DataCache, data_source


class CsvDataProvider:
    def __init__(self, bars_path: str | Path, instruments_path: str | Path):
        self.bars_path = Path(bars_path)
        self.instruments_path = Path(instruments_path)

    def get_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        freq: str = "1d",
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        if freq != "1d":
            raise ValueError("This example provider supports daily bars only")
        frame = pd.read_csv(self.bars_path, parse_dates=["date"])
        requested = set(symbols)
        dates = frame["date"]
        frame = frame.loc[
            frame["symbol"].astype(str).isin(requested)
            & dates.between(pd.Timestamp(start), pd.Timestamp(end))
        ]
        columns = [
            "date",
            "symbol",
            *(fields or ["open", "high", "low", "close", "volume", "amount"]),
        ]
        return frame.loc[:, list(dict.fromkeys(columns))].sort_values(["date", "symbol"])

    def get_symbols(self, universe: str = "all") -> list[str]:
        if universe != "all":
            raise ValueError(f"Unknown universe: {universe}")
        frame = pd.read_csv(self.instruments_path, usecols=["symbol"])
        return sorted(frame["symbol"].dropna().astype(str).unique())

    def get_instruments(self, asof_date: str | None = None) -> pd.DataFrame:
        frame = pd.read_csv(
            self.instruments_path,
            parse_dates=["snapshot_date", "listed_date", "de_listed_date"],
        )
        if asof_date is None:
            return frame
        cutoff = pd.Timestamp(asof_date)
        return frame.loc[
            (frame["listed_date"].isna() | frame["listed_date"].le(cutoff))
            & (frame["de_listed_date"].isna() | frame["de_listed_date"].gt(cutoff))
        ]


def create_engine(bars_path: str | Path, instruments_path: str | Path):
    provider = CsvDataProvider(bars_path, instruments_path)
    source = data_source(
        "custom.csv",
        market=provider,
        instruments=provider,
    )
    return source.create_engine(cache=DataCache(Path("data/cache/custom-csv")))


__all__ = ["CsvDataProvider", "create_engine"]
