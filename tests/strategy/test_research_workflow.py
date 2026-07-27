from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from alphalab.analytics import robustness_report
from alphalab.dataio.factor_returns import build_factor_returns
from alphalab.store import ResultStore
from alphalab.strategy import StrategyConfig, StrategyRepository


def _config(name: str = "test_local") -> StrategyConfig:
    return StrategyConfig.from_yaml_string(
        f"""
name: {name}
description: test
universe:
  pool: all
factors:
  - name: momentum_20d
    weight: 1.0
    source: technical
selection:
  min_factor_coverage: 0.5
  n_stocks: 10
portfolio:
  max_weight: 0.1
  rebalance_freq: monthly
  optimizer: equal_weight
execution:
  cost_bps: 20
"""
    )


def test_strategy_repository_preserves_builtins_and_manages_local_yaml(tmp_path) -> None:
    repository = StrategyRepository(tmp_path)
    assert repository.get("value").built_in is True
    with pytest.raises(PermissionError, match="immutable"):
        repository.save("value", _config("value").to_yaml())

    cloned = repository.clone("value", "value_local")
    assert cloned.built_in is False
    assert cloned.config.name == "value_local"
    edited = repository.save("value_local", _config("value_local").to_yaml())
    assert edited.config.factor_names == ["momentum_20d"]
    assert repository.delete("value_local") is True
    assert repository.get("value_local") is None


def test_robustness_report_uses_benchmark_costs_and_weight_gates() -> None:
    dates = pd.date_range("2021-01-31", periods=60, freq=pd.offsets.MonthEnd())
    strategy = pd.Series(
        np.tile([0.025, 0.005, 0.020, -0.005, 0.015], 12),
        index=dates,
    )
    benchmark = pd.Series(
        np.tile([0.012, 0.003, 0.010, -0.006, 0.007], 12),
        index=dates,
    )
    weights = pd.DataFrame(
        0.1,
        index=dates,
        columns=[f"{value:06d}.SZ" for value in range(10)],
    )
    report = robustness_report(strategy, benchmark, weights, _config())

    assert report["status"] == "research_candidate"
    assert report["metrics"]["excess"]["annual_return"] > 0
    assert set(report["cost_sensitivity"]) == {"10", "20", "50"}
    assert all(check["passed"] for check in report["checks"])

    invalid = weights.copy()
    invalid.iloc[:, 0] = 0.2
    invalid.iloc[:, 1:] = 0.8 / 9
    invalid_report = robustness_report(strategy, benchmark, invalid, _config())
    assert invalid_report["status"] == "invalid"
    assert any(
        check["name"] == "max_weight" and not check["passed"]
        for check in invalid_report["checks"]
    )


def test_runtime_factor_builder_uses_prior_month_characteristics() -> None:
    symbols = [f"{value:06d}.SZ" for value in range(40)]
    dates = pd.date_range("2023-01-02", "2025-03-31", freq="B")
    bar_rows = []
    for symbol_index, symbol in enumerate(symbols):
        base = 10.0 + symbol_index / 10
        for day_index, date in enumerate(dates):
            close = base * (1.0 + 0.0002 * day_index) * (
                1.0 + 0.00005 * symbol_index * np.sin(day_index / 15)
            )
            bar_rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "close": close,
                    "raw_close": close,
                }
            )
    bars = pd.DataFrame(bar_rows)
    fundamentals = pd.DataFrame(
        {
            "available_date": pd.Timestamp("2022-12-31"),
            "quarter": "2022q4",
            "symbol": symbols,
            "shares": [100_000_000 + index * 1_000_000 for index in range(40)],
            "bp": np.linspace(0.2, 1.5, 40),
            "roe": np.linspace(0.03, 0.25, 40),
        }
    )
    risk_free = pd.DataFrame(
        {
            "date": pd.date_range(
                "2023-01-31",
                "2025-03-31",
                freq=pd.offsets.MonthEnd(),
            ),
            "rf": 0.001,
        }
    )

    result = build_factor_returns(bars, fundamentals, risk_free)

    assert set(result) == {"date", "MKT", "SMB", "HML", "MOM", "RMW", "rf"}
    assert len(result) >= 20
    assert result["MKT"].notna().all()
    assert result["HML"].notna().all()
    assert result["rf"].eq(0.001).all()


def test_paper_ledger_tracks_cash_positions_fills_and_nav(tmp_path) -> None:
    store = ResultStore(tmp_path / "app.db")
    try:
        store.ensure_paper_account("test", initial_cash=100_000)
        result = store.apply_paper_orders(
            [{"symbol": "000001.SZ", "action": "buy", "quantity": 100, "price": 10}],
            account_id="test",
            nav_date="2025-01-02",
        )
        assert result["account"]["cash"] == pytest.approx(98_999.7)
        positions = store.list_paper_positions("test")
        assert positions.iloc[0]["quantity"] == 100

        marked = store.mark_paper_positions(
            {"000001.SZ": 12.0},
            "2025-01-03",
            account_id="test",
        )
        assert marked["equity"] == pytest.approx(100_199.7)
        store.apply_paper_orders(
            [{"symbol": "000001.SZ", "action": "sell", "quantity": 100, "price": 12}],
            account_id="test",
            nav_date="2025-01-04",
        )
        assert store.list_paper_positions("test").empty
        assert len(store.list_paper_fills("test")) == 2
        assert len(store.paper_nav("test")) == 3
    finally:
        store.close()


def test_research_run_state_is_persisted_and_cancellable(tmp_path) -> None:
    store = ResultStore(tmp_path / "app.db")
    try:
        run_id = store.create_research_run(
            {
                "strategy_id": "value",
                "profile": "demo",
                "start_date": "2021-07-31",
                "end_date": "2025-12-31",
                "account_id": "paper",
            }
        )
        store.update_research_run(run_id, status="running")
        store.update_research_step(
            run_id,
            "data_status",
            "succeeded",
            {"profile": "demo"},
        )
        assert store.request_research_cancel(run_id) is True
        run = store.get_research_run(run_id)
        assert run["status"] == "running"
        assert run["cancel_requested"] is True
        assert run["steps"][0]["detail"] == {"profile": "demo"}
    finally:
        store.close()


def test_legacy_filled_orders_migrate_to_coherent_paper_ledger(tmp_path) -> None:
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE orders (
             id TEXT PRIMARY KEY, signal_id TEXT, symbol TEXT NOT NULL,
             action TEXT NOT NULL, quantity REAL NOT NULL, price REAL,
             fill_price REAL, fill_quantity REAL, commission REAL,
             status TEXT, broker_order_id TEXT, broker TEXT,
             submitted_at TEXT, filled_at TEXT
           )"""
    )
    connection.executemany(
        """INSERT INTO orders
           (id, symbol, action, quantity, price, fill_price, commission,
            status, broker, submitted_at)
           VALUES (?, ?, 'buy', ?, ?, ?, ?, 'filled', 'paper', ?)""",
        [
            ("old-1", "000001.SZ", 100, 10, 10, 0.3, "2025-01-03"),
            ("old-2", "000002.SZ", 200, 20, 20, 1.2, "2025-01-03"),
        ],
    )
    connection.commit()
    connection.close()

    store = ResultStore(database)
    try:
        account = store.paper_account("paper")
        assert account["cash"] == pytest.approx(994_998.5)
        assert len(store.list_paper_positions("paper")) == 2
        assert len(store.list_paper_fills("paper")) == 2
        assert len(store.paper_nav("paper")) == 1
    finally:
        store.close()

    reopened = ResultStore(database)
    try:
        assert len(reopened.list_paper_fills("paper")) == 2
        assert len(reopened.list_paper_positions("paper")) == 2
    finally:
        reopened.close()
