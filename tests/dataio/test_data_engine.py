from __future__ import annotations

import pandas as pd

from alphalab.dataio import create_default_engine


def test_default_engine_reads_generic_local_parquet(tmp_path):
    market = tmp_path / "market"
    fundamentals = tmp_path / "fundamentals"
    factors = tmp_path / "factors"
    market.mkdir()
    fundamentals.mkdir()
    factors.mkdir()

    pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=3),
            "symbol": ["AAA", "AAA", "AAA"],
            "open": [10, 11, 12],
            "high": [11, 12, 13],
            "low": [9, 10, 11],
            "close": [10.5, 11.5, 12.5],
            "volume": [100, 110, 120],
        }
    ).to_parquet(market / "bars.parquet")
    pd.DataFrame(
        {
            "quarter": ["2023q4", "2024q1"],
            "available_date": pd.to_datetime(["2024-03-15", "2024-05-01"]),
            "symbol": ["AAA", "AAA"],
            "ep": [0.08, 0.10],
            "roe": [0.15, 0.17],
        }
    ).to_parquet(fundamentals / "fundamentals.parquet")
    pd.DataFrame({"MKT": [0.01, 0.02]}, index=pd.to_datetime(["2024-01-31", "2024-02-29"])).to_parquet(
        factors / "factor_returns.parquet"
    )

    engine = create_default_engine(tmp_path)
    assert engine.providers() == {
        "market": ["local"],
        "instrument": ["local"],
        "fundamental": ["local"],
        "factor": ["local"],
        "research": [],
    }
    assert engine.get_symbols() == ["AAA"]
    assert not engine.get_bars(["AAA"], "2024-01-01", "2024-01-03").empty
    before_release = engine.get_fundamentals(
        ["AAA"], ["ep"], "2023q1", "2024q4", asof_date="2024-04-30"
    )
    assert before_release.iloc[-1]["ep"] == 0.08
    after_release = engine.get_fundamentals(
        ["AAA"], ["ep"], "2023q1", "2024q4", asof_date="2024-05-02"
    )
    assert after_release.iloc[-1]["ep"] == 0.10
    assert list(engine.get_factors(["MKT"], "2024-01-01", "2024-12-31").columns) == ["MKT"]
