"""Provider protocols for pluggable data adapters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class MarketDataProvider(Protocol):
    """Pull-based provider for OHLCV bars."""

    def get_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        freq: str = "1d",
        fields: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """Return long-table bars with columns date, symbol, close, etc."""
        ...

    def get_symbols(self, universe: str = "all") -> list[str]:
        """Return symbols available for a universe name."""
        ...


@runtime_checkable
class InstrumentProvider(Protocol):
    """Reference data used to construct an as-of-date investable universe."""

    def get_instruments(self, asof_date: Optional[str] = None) -> pd.DataFrame:
        """Return instrument rows with symbol and listing interval metadata."""
        ...


@runtime_checkable
class FundamentalProvider(Protocol):
    """Pull-based provider for point-in-time fundamental snapshots."""

    def get_fundamentals(
        self,
        symbols: list[str],
        fields: list[str],
        start_quarter: str,
        end_quarter: str,
        asof_date: Optional[str] = None,
        strict: bool = True,
    ) -> pd.DataFrame:
        """Return long-table fundamentals with quarter, symbol and fields."""
        ...


@runtime_checkable
class FactorProvider(Protocol):
    """Pull-based provider for factor return series."""

    def get_factors(
        self,
        names: list[str],
        start: str,
        end: str,
        freq: str = "1M",
        strict: bool = True,
    ) -> pd.DataFrame:
        """Return a DatetimeIndex x factor-name DataFrame."""
        ...

    def get_risk_free_rate(self, start: str, end: str, freq: str = "1M") -> pd.Series:
        """Return a risk-free rate series."""
        ...


@dataclass(frozen=True)
class Tick:
    """A normalized realtime tick."""

    symbol: str
    timestamp: datetime
    price: float
    volume: float = 0.0
    bid: float | None = None
    ask: float | None = None


@runtime_checkable
class RealtimeProvider(Protocol):
    """Optional push-based realtime provider."""

    def subscribe(self, symbols: list[str], on_tick: Callable[[Tick], None]) -> None:
        ...

    def unsubscribe(self, symbols: list[str]) -> None:
        ...

    def is_connected(self) -> bool:
        ...


def to_wide(df: pd.DataFrame, field: str = "close") -> pd.DataFrame:
    """Pivot long-table bars to Date x Symbol."""

    if df.empty:
        return pd.DataFrame()
    return df.pivot(index="date", columns="symbol", values=field).sort_index()


def to_long(wide: pd.DataFrame, field: str = "close") -> pd.DataFrame:
    """Melt Date x Symbol data into a long table."""

    if wide.empty:
        return pd.DataFrame(columns=["date", "symbol", field])
    out = wide.stack().rename(field).reset_index()
    out.columns = ["date", "symbol", field]
    return out.sort_values(["date", "symbol"]).reset_index(drop=True)

