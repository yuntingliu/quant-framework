from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alphalab.analytics import evaluate_factor
from alphalab.dataio import create_default_engine
from alphalab.engine import SignalEngine, run_backtest_detailed
from alphalab.factors.expression import FactorExpressionError, evaluate_factor_expression
from alphalab.strategy import FactorSpec, StrategyConfig


def _write_trending_fixture(root, symbols: int = 20) -> None:
    market = root / "market"
    fundamentals = root / "fundamentals"
    factors = root / "factors"
    market.mkdir()
    fundamentals.mkdir()
    factors.mkdir()
    dates = pd.bdate_range("2021-01-04", "2024-12-31")
    rows = []
    for index in range(symbols):
        symbol = f"S{index:03d}"
        daily_drift = 0.00005 + index * 0.00001
        close = 10.0 * np.power(1.0 + daily_drift, np.arange(len(dates)))
        for date, price in zip(dates, close):
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "open": price * 0.999,
                    "high": price * 1.002,
                    "low": price * 0.998,
                    "close": price,
                    "volume": 1_000_000.0,
                    "amount": price * 1_000_000.0,
                }
            )
    pd.DataFrame(rows).to_parquet(market / "bars.parquet")
    pd.DataFrame(columns=["quarter", "available_date", "symbol"]).to_parquet(
        fundamentals / "fundamentals.parquet"
    )
    pd.DataFrame(index=pd.DatetimeIndex([], name="date")).to_parquet(
        factors / "factor_returns.parquet"
    )


def test_safe_factor_expression_combines_only_registered_inputs() -> None:
    values = {
        "momentum_60d": pd.Series({"AAA": 0.2, "BBB": 0.1}),
        "volatility_20d": pd.Series({"AAA": 0.3, "BBB": 0.1}),
    }
    result = evaluate_factor_expression(
        "zscore(momentum_60d) - 0.5 * zscore(volatility_20d)",
        values,
    )
    assert list(result.index) == ["AAA", "BBB"]
    assert result["AAA"] > result["BBB"]
    with pytest.raises(FactorExpressionError):
        evaluate_factor_expression("__import__('os').system('whoami')", values)


def test_weight_cap_holds_cash_when_selected_names_lack_capacity(tmp_path) -> None:
    _write_trending_fixture(tmp_path, symbols=3)
    engine = create_default_engine(tmp_path)
    config = StrategyConfig.from_yaml_string(
        """
name: capped
universe:
  pool: all
  min_history_days: 20
selection:
  n_stocks: 3
portfolio:
  max_weight: 0.10
factors:
  - name: momentum_20d
    source: technical
    weight: 1.0
"""
    )
    signal_engine = SignalEngine(engine)
    weights = signal_engine.generate_targets(config, "2024-12-31")
    assert sum(weights.values()) == pytest.approx(0.30)
    assert max(weights.values()) <= 0.10
    assert signal_engine.diagnostics["cash_weight"] == pytest.approx(0.70)


def test_signal_eligibility_is_computed_from_information_available_as_of_date(tmp_path) -> None:
    _write_trending_fixture(tmp_path, symbols=3)
    bars_path = tmp_path / "market" / "bars.parquet"
    bars = pd.read_parquet(bars_path)
    bars.loc[bars["symbol"].eq("S000"), "close"] = 1.0
    bars.loc[bars["symbol"].eq("S001") & bars["date"].eq(bars["date"].max()), "volume"] = 0.0
    bars.to_parquet(bars_path)
    config = StrategyConfig.from_yaml_string(
        """
name: eligible
universe:
  pool: all
  min_price: 5
  min_history_days: 20
  require_positive_volume: true
selection:
  n_stocks: 1
portfolio:
  max_weight: 1.0
factors:
  - name: momentum_20d
    source: technical
    weight: 1.0
"""
    )
    signal_engine = SignalEngine(create_default_engine(tmp_path))
    weights = signal_engine.generate_targets(config, "2024-12-31")
    assert set(weights) == {"S002"}
    assert signal_engine.diagnostics["exclusions"] == {
        "minimum_price": 1,
        "not_trading": 1,
    }


def test_instrument_listing_intervals_filter_the_historical_universe(tmp_path) -> None:
    _write_trending_fixture(tmp_path, symbols=3)
    instruments = tmp_path / "instruments"
    instruments.mkdir()
    pd.DataFrame(
        {
            "snapshot_date": pd.Timestamp("2025-01-31"),
            "symbol": ["S000", "S001", "S002"],
            "listed_date": [
                pd.Timestamp("2025-01-02"),
                pd.Timestamp("2020-01-01"),
                pd.Timestamp("2020-01-01"),
            ],
            "de_listed_date": [pd.NaT, pd.NaT, pd.NaT],
        }
    ).to_parquet(instruments / "instruments.parquet")
    config = StrategyConfig.from_yaml_string(
        """
name: listing_intervals
universe:
  pool: all
  min_history_days: 20
selection:
  n_stocks: 3
portfolio:
  max_weight: 0.5
factors:
  - name: momentum_20d
    source: technical
    weight: 1.0
"""
    )
    signal_engine = SignalEngine(create_default_engine(tmp_path))
    weights = signal_engine.generate_targets(config, "2024-12-31")
    assert set(weights) == {"S001", "S002"}
    assert signal_engine.diagnostics["instrument_filter_applied"] is True


def test_factor_research_reports_ic_quantiles_decay_and_bootstrap(tmp_path) -> None:
    _write_trending_fixture(tmp_path)
    result = evaluate_factor(
        create_default_engine(tmp_path),
        FactorSpec(name="momentum_20d", weight=1.0),
        "2022-01-01",
        "2024-12-31",
    )
    assert result["periods"] >= 24
    assert result["summary"]["ic_mean"] > 0.9
    assert result["summary"]["bootstrap_ic_95"]["lower"] > 0
    assert set(result["decay"]) == {"1", "3", "6"}
    assert len(result["rows"][0]["quantile_returns"]) == 5


def test_backtest_executes_next_session_and_respects_liquidity_capacity(tmp_path) -> None:
    _write_trending_fixture(tmp_path, symbols=3)
    bars_path = tmp_path / "market" / "bars.parquet"
    bars = pd.read_parquet(bars_path)
    bars["amount"] = 10_000.0
    bars.to_parquet(bars_path)
    config = StrategyConfig.from_yaml_string(
        """
name: constrained_execution
universe:
  pool: all
  min_history_days: 20
selection:
  n_stocks: 1
portfolio:
  max_weight: 1.0
  rebalance_freq: monthly
execution:
  cost_bps: 10
  slippage_bps: 5
  impact_bps: 10
  execution_price: next_open
  portfolio_value: 1000000
  max_participation_rate: 0.10
factors:
  - name: momentum_20d
    source: technical
    weight: 1.0
"""
    )
    result = run_backtest_detailed(
        config,
        "2023-01-01",
        "2024-12-31",
        data_engine=create_default_engine(tmp_path),
    )
    assert not result.returns.empty
    assert result.executions
    first = result.executions[0]
    assert first["entry_date"] > first["signal_date"]
    assert first["constrained_symbols"]
    assert first["total_cost"] > 0
    assert first["cash_weight"] >= first["total_cost"]
    assert result.weights.iloc[0].sum() <= 0.001000001
