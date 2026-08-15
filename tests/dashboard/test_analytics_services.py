from __future__ import annotations

import pandas as pd
import pytest

from dashboard.backend.services import (
    backtest_analytics_service,
    market_analytics_service,
)


@pytest.fixture
def factor_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "MKT": [0.10, -0.10, 0.20, 0.00],
            "SMB": [0.02, 0.01, -0.01, 0.03],
            "HML": [-0.02, 0.00, 0.01, 0.02],
        },
        index=pd.date_range("2023-01-31", periods=4, freq=pd.offsets.MonthEnd()),
    )


def _install_factor_frame(monkeypatch, frame: pd.DataFrame) -> None:
    def load(_profile="demo", _start=None, _end=None, factors=None):
        if factors is None:
            return frame.copy()
        return frame[list(factors)].copy()

    monkeypatch.setattr(market_analytics_service, "_load_factor_frame", load)


def test_market_analytics_compound_drawdown_and_correlation(monkeypatch, factor_frame):
    _install_factor_frame(monkeypatch, factor_frame)

    cumulative = market_analytics_service.compute_cumulative_returns()
    assert cumulative["series"]["MKT"][-1] == pytest.approx(0.188)

    annual = market_analytics_service.compute_annual_returns()
    assert annual["years"] == ["2023"]
    assert annual["series"]["MKT"] == pytest.approx([0.188])

    drawdowns = market_analytics_service.compute_drawdowns()
    assert drawdowns["drawdown_values"][1] == pytest.approx(-0.10)
    assert drawdowns["top_drawdowns"][0]["depth"] == pytest.approx(-0.10)

    correlation = market_analytics_service.compute_correlation()
    assert correlation["labels"] == ["MKT", "SMB", "HML"]
    for index in range(3):
        assert correlation["matrix"][index][index] == pytest.approx(1.0)


def test_market_analytics_kpi_stats_and_empty_frame(monkeypatch, factor_frame):
    _install_factor_frame(monkeypatch, factor_frame)

    kpi = market_analytics_service.compute_kpi()
    assert kpi["n_months"] == 4
    assert kpi["mkt_ann_return"] is not None
    assert kpi["mkt_sharpe_full"] is not None

    stats = market_analytics_service.compute_factor_stats()
    assert {row["factor"] for row in stats["stats"]} == {"MKT", "SMB", "HML"}
    assert all(row["max_dd"] is not None for row in stats["stats"])
    selected_stats = market_analytics_service.compute_factor_stats(factors=["SMB"])
    assert [row["factor"] for row in selected_stats["stats"]] == ["SMB"]

    monkeypatch.setattr(
        market_analytics_service,
        "_load_factor_frame",
        lambda *_args, **_kwargs: pd.DataFrame(columns=["MKT", "SMB", "HML"]),
    )
    empty = market_analytics_service.compute_cumulative_returns()
    assert empty["dates"] == []
    assert empty["series"] == {"MKT": [], "SMB": [], "HML": []}


def test_custom_market_risk_factor_is_a_safe_linear_return_combination(
    monkeypatch,
    factor_frame,
):
    _install_factor_frame(monkeypatch, factor_frame)

    result = market_analytics_service.compute_custom_risk_factor(
        "market_plus_size",
        "0.75 * MKT + 0.25 * SMB",
    )

    expected = 0.75 * factor_frame["MKT"] + 0.25 * factor_frame["SMB"]
    assert result["dependencies"] == ["MKT", "SMB"]
    assert result["returns"] == pytest.approx(expected.tolist())
    assert result["summary"]["observations"] == len(factor_frame)
    assert result["summary"]["annual_volatility"] is not None

    with pytest.raises(ValueError, match="multiply two return series"):
        market_analytics_service.compute_custom_risk_factor("invalid", "MKT * SMB")
    with pytest.raises(ValueError, match="unknown market risk return inputs"):
        market_analytics_service.compute_custom_risk_factor("invalid", "UNKNOWN + MKT")


def test_market_factor_parser_preserves_canonical_lowercase_risk_free_name():
    assert market_analytics_service._parse_factors(["mkt", "RF", "rf"]) == ["MKT", "rf"]


def test_backtest_analysis_derives_equity_turnover_and_holdings():
    record = {
        "id": "bt-1",
        "strategy_id": "balanced",
        "start_date": "2024-01-31",
        "end_date": "2024-02-29",
        "run_at": "2024-03-01",
        "metrics": {"total_return": 0.089, "sharpe": 1.2},
        "returns": [
            {"date": "2024-01-31", "value": 0.10, "benchmark": 0.05},
            {"date": "2024-02-29", "value": -0.01, "benchmark": 0.01},
        ],
        "weights": [
            {"date": "2024-01-31", "symbol": "A", "weight": 0.60},
            {"date": "2024-01-31", "symbol": "B", "weight": 0.40},
            {"date": "2024-02-29", "symbol": "A", "weight": 0.50},
            {"date": "2024-02-29", "symbol": "B", "weight": 0.50},
        ],
    }

    analysis = backtest_analytics_service.analyze_record(record)

    assert analysis["equity_curve"] == pytest.approx([1.10, 1.089])
    assert analysis["drawdown"][-1] == pytest.approx(-0.01)
    assert analysis["benchmark_equity_curve"] == pytest.approx([1.05, 1.0605])
    assert analysis["excess_equity_curve"] == pytest.approx([1.05, 1.029])
    assert analysis["benchmark_coverage"] == pytest.approx(1.0)
    assert analysis["has_execution_audit"] is False
    assert analysis["turnover"][0]["value"] == pytest.approx(1.00)
    assert analysis["turnover"][1]["value"] == pytest.approx(0.10)
    assert analysis["holdings"][0]["holdings_count"] == 2
    assert analysis["holdings"][0]["concentration"] == pytest.approx(0.52)


def test_backtest_analysis_handles_empty_results_and_compare_alignment(monkeypatch):
    empty = backtest_analytics_service.analyze_record(
        {
            "id": "empty",
            "strategy_id": "empty",
            "metrics": {},
            "returns": [],
            "weights": [],
        }
    )
    assert empty["dates"] == []
    assert empty["average_turnover"] is None
    assert empty["benchmark_coverage"] is None

    analyses = {
        "a": {
            "id": "a",
            "strategy_id": "alpha",
            "start_date": "2024-01-31",
            "end_date": "2024-02-29",
            "run_at": "2024-03-01",
            "dates": ["2024-01-31", "2024-02-29"],
            "equity_curve": [1.1, 1.2],
            "metrics": {"sharpe": 1.0},
        },
        "b": {
            "id": "b",
            "strategy_id": "beta",
            "start_date": "2024-02-29",
            "end_date": "2024-03-31",
            "run_at": "2024-04-01",
            "dates": ["2024-02-29", "2024-03-31"],
            "equity_curve": [0.9, 1.0],
            "metrics": {"sharpe": 0.5},
        },
    }
    monkeypatch.setattr(
        backtest_analytics_service,
        "analyze_backtest",
        lambda backtest_id: analyses[backtest_id],
    )
    comparison = backtest_analytics_service.compare_backtests(["a", "b"])
    assert comparison["dates"] == ["2024-01-31", "2024-02-29", "2024-03-31"]
    assert comparison["series"]["a"] == [1.1, 1.2, None]
    assert comparison["series"]["b"] == [None, 0.9, 1.0]
    assert comparison["labels"]["a"] == "alpha · 2024-01–2024-02"
    assert comparison["metrics"][0]["start_date"] == "2024-01-31"
