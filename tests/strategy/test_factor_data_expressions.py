from __future__ import annotations

import numpy as np
import pandas as pd

from alphalab.factors.cross_sectional import compute_cross_sectional_factor
from alphalab.strategy import FactorSpec


def _market_frames() -> dict[str, pd.DataFrame]:
    return {
        symbol: pd.DataFrame(
            [
                {
                    "date": "2025-01-02",
                    "open": open_price,
                    "high": close_price + 1,
                    "low": open_price - 1,
                    "close": close_price,
                    "volume": volume,
                    "amount": close_price * volume,
                    "num_trades": volume / 10.0,
                }
            ]
        )
        for symbol, open_price, close_price, volume in (
            ("AAA", 10.0, 11.0, 100.0),
            ("BBB", 20.0, 19.0, 300.0),
            ("CCC", 30.0, 33.0, 200.0),
        )
    }


def test_expression_accepts_public_market_api_fields():
    factor = FactorSpec(
        name="intraday_liquidity",
        weight=1.0,
        source="expression",
        expression="zscore((close - open) / open) + zscore(log(amount))",
        winsorize=0.0,
    )

    values = compute_cross_sectional_factor(factor, _market_frames(), pd.DataFrame())

    assert list(values.index) == ["AAA", "BBB", "CCC"]
    assert np.isfinite(values.to_numpy()).all()


def test_expression_accepts_schema_discovered_market_fields():
    factor = FactorSpec(
        name="trade_activity",
        weight=1.0,
        source="expression",
        expression="zscore(log(num_trades))",
        winsorize=0.0,
    )

    values = compute_cross_sectional_factor(factor, _market_frames(), pd.DataFrame())

    assert list(values.index) == ["AAA", "BBB", "CCC"]
    assert np.isfinite(values.to_numpy()).all()


def test_expression_accepts_point_in_time_fundamental_api_fields():
    factor = FactorSpec(
        name="capital_scale",
        weight=1.0,
        source="expression",
        expression="zscore(log(shares)) - zscore(log(market_cap))",
        winsorize=0.0,
    )
    fundamentals = pd.DataFrame(
        [
            {"symbol": "AAA", "quarter": "2024q4", "available_date": "2025-03-01", "shares": 10.0, "market_cap": 100.0},
            {"symbol": "BBB", "quarter": "2024q4", "available_date": "2025-03-01", "shares": 20.0, "market_cap": 300.0},
            {"symbol": "CCC", "quarter": "2024q4", "available_date": "2025-03-01", "shares": 40.0, "market_cap": 500.0},
        ]
    )

    values = compute_cross_sectional_factor(factor, _market_frames(), fundamentals)

    assert set(values.index) == {"AAA", "BBB", "CCC"}
    assert np.isfinite(values.to_numpy()).all()


def test_expression_accepts_schema_discovered_fundamental_fields():
    factor = FactorSpec(
        name="cash_quality",
        weight=1.0,
        source="expression",
        expression="zscore(vendor_cash_metric)",
        winsorize=0.0,
    )
    fundamentals = pd.DataFrame(
        [
            {"symbol": "AAA", "vendor_cash_metric": 1.0},
            {"symbol": "BBB", "vendor_cash_metric": 2.0},
            {"symbol": "CCC", "vendor_cash_metric": 4.0},
        ]
    )

    values = compute_cross_sectional_factor(factor, _market_frames(), fundamentals)

    assert set(values.index) == {"AAA", "BBB", "CCC"}
    assert np.isfinite(values.to_numpy()).all()
