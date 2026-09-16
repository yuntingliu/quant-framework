"""Small deterministic adapter example for tests and contributor experiments."""
from __future__ import annotations

import pandas as pd

from alphalab import DataEngine


class FakeMarketProvider:
    def __init__(self) -> None:
        self._bars = pd.DataFrame(
            [
                {
                    "date": date,
                    "symbol": symbol,
                    "open": close - 0.2,
                    "high": close + 0.4,
                    "low": close - 0.4,
                    "close": close,
                    "volume": 1_000_000,
                    "amount": close * 1_000_000,
                }
                for date, close in (("2026-01-05", 10.0), ("2026-01-06", 10.5))
                for symbol in ("000001.SZ", "600000.SH")
            ]
        )

    def get_symbols(self, universe: str = "all") -> list[str]:
        del universe
        return sorted(self._bars["symbol"].unique().tolist())

    def get_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        freq: str = "1d",
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        if freq != "1d":
            raise ValueError("This example supports daily bars only")
        selected = self._bars[
            self._bars["symbol"].isin(symbols)
            & self._bars["date"].between(start, end)
        ].copy()
        columns = ["date", "symbol", *(fields or ["open", "high", "low", "close", "volume", "amount"])]
        return selected[columns].reset_index(drop=True)


provider = FakeMarketProvider()
engine = DataEngine()
engine.register_market("fake", provider, default=True)

print(engine.get_bars(provider.get_symbols(), "2026-01-05", "2026-01-06"))
