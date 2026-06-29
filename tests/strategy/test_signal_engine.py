from __future__ import annotations

import numpy as np
import pandas as pd

from alphalab import SignalEngine, StrategyConfig, run_backtest
from alphalab.dataio import create_default_engine


def _write_market_fixture(root):
    market = root / "market"
    fundamentals = root / "fundamentals"
    factors = root / "factors"
    market.mkdir()
    fundamentals.mkdir()
    factors.mkdir()
    dates = pd.bdate_range("2023-01-02", "2023-08-31")
    rows = []
    for idx, symbol in enumerate(["AAA", "BBB", "CCC"]):
        base = 10 + idx
        trend = 1 + np.linspace(0, 0.40 - idx * 0.10, len(dates))
        close = base * trend
        for date, price in zip(dates, close):
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "close": price,
                    "volume": 1000 + idx * 100,
                }
            )
    pd.DataFrame(rows).to_parquet(market / "bars.parquet")
    pd.DataFrame({"quarter": ["2023q2"] * 3, "symbol": ["AAA", "BBB", "CCC"], "ep": [0.06, 0.09, 0.04]}).to_parquet(
        fundamentals / "fundamentals.parquet"
    )
    pd.DataFrame(index=pd.DatetimeIndex([], name="date")).to_parquet(factors / "factor_returns.parquet")


def test_signal_engine_generates_targets(tmp_path):
    _write_market_fixture(tmp_path)
    engine = create_default_engine(tmp_path)
    config = StrategyConfig.from_yaml_string(
        """
name: toy
universe:
  symbols: [AAA, BBB, CCC]
selection:
  n_stocks: 2
portfolio:
  max_weight: 0.50
factors:
  - name: momentum_20d
    source: technical
    direction: long
    weight: 1.0
"""
    )
    targets = SignalEngine(engine).generate_targets(config, "2023-08-31")
    assert targets
    assert abs(sum(targets.values()) - 1.0) < 1e-9
    assert max(targets.values()) <= 0.50


def test_run_backtest_returns_series_and_weights(tmp_path):
    _write_market_fixture(tmp_path)
    engine = create_default_engine(tmp_path)
    config = StrategyConfig.from_yaml_string(
        """
name: toy
universe:
  symbols: [AAA, BBB, CCC]
selection:
  n_stocks: 2
portfolio:
  max_weight: 0.50
factors:
  - name: momentum_20d
    source: technical
    direction: long
    weight: 1.0
"""
    )
    returns, weights = run_backtest(config, "2023-03-01", "2023-08-31", data_engine=engine)
    assert not returns.empty
    assert not weights.empty

