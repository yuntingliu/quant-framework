from __future__ import annotations

import pandas as pd

from alphalab.analytics.robustness import equal_weight_benchmark


class _BenchmarkEngine:
    def __init__(self) -> None:
        self.sessions = pd.bdate_range("2024-01-01", periods=5)
        self.bars = pd.DataFrame(
            [
                {
                    "date": date,
                    "symbol": symbol,
                    "open": price,
                    "close": price,
                }
                for symbol, prices in {
                    "A": [10.0, 11.0, 12.0, 13.0, 14.0],
                    "B": [20.0, 18.0, 18.0, 18.0, 18.0],
                }.items()
                for date, price in zip(self.sessions, prices, strict=True)
            ]
        )

    def get_bars(self, symbols, start, end, **kwargs):
        return self.bars.loc[
            self.bars["symbol"].isin(symbols)
            & self.bars["date"].between(pd.Timestamp(start), pd.Timestamp(end))
        ].copy()

    def get_instruments(self, as_of_date=None):
        return pd.DataFrame(
            {
                "symbol": ["A", "B"],
                "listed_date": [self.sessions[0], self.sessions[2]],
                "de_listed_date": [pd.NaT, pd.NaT],
            }
        )


def test_daily_equal_weight_benchmark_is_vectorized_and_listing_aware():
    engine = _BenchmarkEngine()
    result = equal_weight_benchmark(
        engine,
        ["A", "B"],
        str(engine.sessions[0].date()),
        str(engine.sessions[-1].date()),
        frequency="daily",
        execution_price="next_open",
    )

    assert list(result.index) == list(engine.sessions[1:4])
    assert result.iloc[0] == 12.0 / 11.0 - 1.0
    assert result.iloc[1] == 13.0 / 12.0 - 1.0
    assert result.iloc[2] == ((14.0 / 13.0 - 1.0) + 0.0) / 2.0
