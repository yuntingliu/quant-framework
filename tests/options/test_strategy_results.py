"""Saved option-strategy presentation uses frozen engine outputs only."""

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers.options import router
from apps.api.services import options_strategy_service as service

SOURCE = """
@signal(schedule=Weekly.last_trading_day(at="close"))
def select(context, *, top_n=3):
    universe = [s for s in context.universe if s in ["510050.SH", "510300.SH"]]
    return context.combine_factors(weights={"option_smirk": -1}, normalization="rank")
"""


@pytest.fixture
def stored(monkeypatch):
    record = {
        "id": "saved-run",
        "profile": "runtime",
        "project_id": "editable-project",
        "project_strategy_id": "main",
        "strategy_source": SOURCE,
        "strategy_revision": 3,
        "strategy_source_sha256": "frozen-hash",
        "settings": {"benchmark_symbols": ["510300.SH"]},
        "returns": [
            {"date": "2025-01-02", "value": -0.1, "benchmark": None},
            {"date": "2025-01-03", "value": 0.2, "benchmark": 0.1},
            {"date": "2025-01-06", "value": 0.1, "benchmark": None},
            {"date": "2025-01-07", "value": -0.05, "benchmark": 0.2},
        ],
        "weights": [{"date": "2025-01-06", "symbol": "510050.SH", "weight": 0.3}],
        "executions": [
            {
                "decision_date": "2025-01-02",
                "entry_date": "2025-01-03",
                "activation": "next_session_close",
                "successful_trade_count": 2,
                "total_cost": 0.001,
                "turnover": 0.4,
            }
        ],
    }
    monkeypatch.setattr(service.result_service, "get_backtest", lambda _: deepcopy(record))
    monkeypatch.setattr(
        service.result_service,
        "build_backtest_summary",
        lambda r: {
            "period": {"start_date": "2025-01-02", "end_date": "2025-01-07"},
            "metrics": {"total_return": 0.1286},
            "warnings": ["execution warning"],
            "execution_reliable": False,
        },
    )
    service.strategy_result.cache_clear()
    yield record
    service.strategy_result.cache_clear()


def test_frozen_rule_is_literal_and_never_executes_source():
    rule = service.frozen_rule(SOURCE)
    assert rule["weights"] == {"option_smirk": -1}
    assert rule["top_n"] == 3
    assert rule["frequency"] == "weekly"
    assert rule["symbols"] == ["510050.SH", "510300.SH"]
    assert service.frozen_rule(SOURCE.replace('{"option_smirk": -1}', "dangerous()")) is None
    assert service.frozen_rule(SOURCE.replace("option_smirk", "momentum")) is None


def test_allocation_cap_is_read_from_frozen_portfolio_without_guessing():
    portfolio = '''
@portfolio(id="allocation")
def allocate(context, signal, state, *, max_weight=0.2):
    """Saved allocation rule."""
    chosen = list(signal.selected)
    weight = min(max_weight, 1/len(chosen)) if chosen else 0.0
    return PortfolioDecision(target_weights={s: weight for s in chosen}, state=state)
'''
    rule = service.frozen_rule(SOURCE + portfolio)
    assert rule["allocation"] == "capped_equal_weight"
    assert rule["max_weight"] == 0.2
    edited = service.frozen_rule(SOURCE + portfolio.replace("min(max_weight, 1/len(chosen))", "0.5"))
    assert edited["allocation"] == "custom"
    assert edited["max_weight"] is None


def test_saved_nav_drawdown_matching_dates_and_costs(stored):
    result = service.strategy_result("saved-run")
    assert result["curve"][0]["drawdown"] == pytest.approx(-0.1)
    assert result["curve"][-1]["nav"] == pytest.approx(0.9 * 1.2 * 1.1 * 0.95)
    assert result["common_period"]["sessions"] == 2
    assert result["common_period"]["strategy_return"] == pytest.approx(1.2 * 0.95 - 1)
    assert result["common_period"]["benchmark_return"] == pytest.approx(1.1 * 1.2 - 1)
    # An internal gap must not be joined into a fabricated continuous benchmark NAV.
    assert [r["benchmark_nav"] for r in result["curve"]] == [None, 1.1, None, None]
    assert result["annual"][0]["return"] == pytest.approx(result["curve"][-1]["nav"] - 1)
    assert result["trading"]["sum_period_cost_rates"] == 0.001
    assert result["trading"]["successful_trades"] == 2
    assert result["positions"][0]["name_zh"] == "上证50ETF"
    assert result["warnings"] == ["execution warning"]
    assert result["rule"]["execution"] == "next_session_close"
    assert result["source_sha256"] == "frozen-hash"


def test_unsupported_saved_run_is_not_relabelled_as_option_strategy(stored):
    stored["strategy_source"] = SOURCE.replace("option_smirk", "momentum")
    with pytest.raises(ValueError, match="frozen option"):
        service.strategy_result("unrelated-run")


def test_strategy_detail_api_and_unrelated_run_error(stored):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/api/options/research/strategies/saved-run")
        assert response.status_code == 200
        assert response.json()["name_en"] == "Low-smirk ETF rotation"
        assert "strategy_source" not in response.json()
        service.strategy_result.cache_clear()
        stored["strategy_source"] = "x = 1"
        assert client.get("/api/options/research/strategies/other-run").status_code == 422
