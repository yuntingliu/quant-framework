"""Regression cases from the module audit, including rare-leg masking."""

import numpy as np
import pandas as pd
import pytest

from alphalab.dataio.option_features import _digest
from alphalab.options import (
    EuropeanOption,
    black_scholes,
    joint_monte_carlo,
    merton_prices,
    research_panel,
    ssvi_iv,
    ssvi_surface,
)
from apps.api.services import options_strategy_service as strategies


def rare_leg(kind="P", strike=50.0, quantity=-100000.0):
    o = EuropeanOption(100, strike, 1, 0, 0.2, kind)
    return dict(
        status="eligible",
        underlying="510050.XSHG",
        quantity=quantity,
        multiplier=1.0,
        maturity=1.0,
        spot=100.0,
        strike=strike,
        rate=0.0,
        iv=0.2,
        option_type=kind,
        effective_dividend_yield=0.0,
        market_price=black_scholes(o),
    )


def test_rare_leg_cannot_be_hidden_by_an_active_leg():
    legs = [rare_leg("C", 1, 1), rare_leg()]
    assets = {
        "510050.XSHG": dict(
            spot=100.0, volatility=0.2, forwards=[dict(maturity=1.0, forward=100.0)]
        )
    }
    r = joint_monte_carlo(legs, assets, paths=5000, seed=42, jump_intensity=0)
    assert r["warning"] == "rare_payoff_insufficient_sampling"
    assert r["ci95"] is None
    assert r["leg_diagnostics"][1]["positive_payoffs"] == 0
    improved = joint_monte_carlo(
        legs,
        assets,
        paths=100000,
        seed=42,
        jump_intensity=0,
        sampling="importance",
        importance_tilt=-2.5,
    )
    exact = sum(leg["market_price"] * leg["quantity"] for leg in legs)
    assert abs(improved["value"] - exact) < 5 * improved["standard_error"]
    assert improved["leg_diagnostics"][1]["positive_payoffs"] > 100
    assert improved["effective_paths"] > 100


def test_missing_session_never_shifts_a_return_label_to_a_later_open():
    days = pd.bdate_range("2025-01-06", periods=4)
    f = pd.DataFrame({"date": days, "symbol": "A", "option_smirk": 0.1})
    b = pd.DataFrame(
        {"date": days, "symbol": "A", "open": [100, 110, 120, 130], "close": [101, 111, 121, 131]}
    ).drop(index=1)
    r = research_panel(f, b, horizon=1)
    assert r.iloc[0].label_start == days[1]
    assert pd.isna(r.iloc[0].target_return)
    assert r.iloc[2].target_return == pytest.approx(131 / 130 - 1)
    r = research_panel(f, b, horizon=5)
    assert r.target_return.isna().all()


def test_retrieval_metadata_does_not_change_economic_input_hash():
    a = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-06"]),
            "close": [0.2],
            "retrieved_at": ["old"],
            "expected_sessions": ["old"],
        }
    )
    b = a.copy()
    b["retrieved_at"] = "new"
    b["expected_sessions"] = "new"
    assert _digest(a) == _digest(b)
    b["close"] = 0.3
    assert _digest(a) != _digest(b)


def test_frozen_rules_preserve_direction_and_schedule_changes():
    source = """
@signal(schedule=Weekly.first_trading_day(at="open"))
def select(context, *, top_n=3):
    scores=context.combine_factors(weights={"option_smirk":-1},normalization="zscore").dropna()
    selected=list(scores.nsmallest(top_n).index)
    return SignalResult(selected=selected, scores=scores, state=state)
"""
    r = strategies.frozen_rule(source)
    assert (
        r["selection"] == "lowest" and r["schedule_day"] == "first" and r["decision_time"] == "open"
    )
    assert r["normalization"] == "zscore"


def test_ssvi_constraints_and_domain_are_enforced_between_quotes():
    fits = []
    for t, vol in [(0.1, 0.2), (0.3, 0.22), (0.5, 0.21)]:
        fits.append(
            dict(
                maturity=t,
                expiry=str(t),
                k_min=-0.2,
                k_max=0.2,
                forward=100.0,
                observed=[dict(k=float(k), iv=vol) for k in np.linspace(-0.2, 0.2, 17)],
            )
        )
    r = ssvi_surface({"fits": fits})
    assert r["constraints"]["max_theta_phi_one_plus_abs_rho"] < 4
    assert r["constraints"]["max_theta_phi_squared_one_plus_abs_rho"] < 4
    assert np.all(np.diff(r["parameters"]["theta"]) >= 0)
    assert ssvi_iv(r, 0.2, 0) == pytest.approx(
        np.sqrt(np.interp(0.2, r["parameters"]["maturities"], r["parameters"]["theta"]) / 0.2)
    )
    with pytest.raises(ValueError, match="outside"):
        ssvi_iv(r, 0.2, 0.3)


def test_merton_poisson_price_recovers_bs_zero_jump_limit():
    rows = [
        EuropeanOption(100, k, 0.5, 0.02, 0.2, kind, 0.01)
        for k in [80, 100, 120]
        for kind in ["C", "P"]
    ]
    values = merton_prices(
        [r.strike for r in rows],
        [r.maturity for r in rows],
        [r.rate for r in rows],
        [100 * np.exp(0.01 * 0.5)] * len(rows),
        [r.option_type for r in rows],
        [0.2, 0.0, -0.1, 0.2],
    )
    assert values == pytest.approx([black_scholes(r) for r in rows], abs=1e-10)


def test_custom_signal_body_is_not_described_as_a_standard_selection():
    source = """
@signal(schedule=Weekly.last_trading_day(at="close"))
def select(context, state, *, top_n=3):
    scores=context.combine_factors(weights={"option_smirk":-1},normalization="rank").dropna()
    selected=list(scores.nlargest(top_n).index)
    selected=[]
    return SignalResult(selected=selected,scores=scores,state=state)
"""
    assert strategies.frozen_rule(source)["selection"] == "custom"


def test_ssvi_query_rejects_failed_adjacent_tenor_without_hiding_good_tenors():
    f = [
        dict(
            maturity=t,
            expiry=str(t),
            k_min=-0.2,
            k_max=0.2,
            forward=100.0,
            observed=[dict(k=float(k), iv=0.2) for k in np.linspace(-0.2, 0.2, 9)],
        )
        for t in [0.1, 0.3, 0.5]
    ]
    result = ssvi_surface({"fits": f})
    result["fits"][0]["calibration_usable"] = False
    with pytest.raises(ValueError, match="unreliable"):
        ssvi_iv(result, 0.2, 0.0)
    assert ssvi_iv(result, 0.4, 0.0) > 0


def test_comparison_benchmark_uses_close_returns_labeled_at_period_end():
    from types import SimpleNamespace

    from alphalab.analytics import equal_weight_benchmark

    days = pd.bdate_range("2025-01-06", periods=5)
    bars = pd.DataFrame(
        {
            "date": days,
            "symbol": "510050.SH",
            "open": 90.0,
            "close": [100.0, 101.0, 102.0, 103.0, 104.0],
        }
    )
    engine = SimpleNamespace(
        get_bars=lambda *a, **kw: bars, get_instrument_master=lambda: pd.DataFrame()
    )
    result = equal_weight_benchmark(
        engine,
        ["510050.SH"],
        "2025-01-06",
        "2025-01-10",
        frequency="daily",
        execution_price="close",
        label_period_end=True,
    )
    assert result.index[0] == days[2]
    assert result.iloc[0] == pytest.approx(102 / 101 - 1)
    assert result.index[-1] == days[-1]


def test_common_comparison_reports_actual_period_and_counts_cash_days(monkeypatch):
    from apps.api.services import options_comparison_service as service

    days = pd.bdate_range("2025-01-06", periods=3)
    card = dict(
        backtest_id="run", name_zh="策略", name_en="Strategy", rule={"symbols": ["510050.SH"]}
    )
    record = dict(
        returns=[dict(date=str(d.date()), value=0.01) for d in days],
        weights=[dict(date=str(days[0].date()), symbol="510050.SH", weight=0.6)],
    )
    monkeypatch.setattr(
        service.options_strategy_service, "strategies", lambda: {"strategies": [card]}
    )
    monkeypatch.setattr(service.result_service, "get_backtest", lambda _: record)
    monkeypatch.setattr(service, "_engine", lambda _: None)

    def benchmark(*args, **kwargs):
        assert kwargs["label_period_end"] is True
        assert kwargs["execution_price"] == "close"
        assert pd.Timestamp(args[2]) < days[0]
        return pd.Series(0.005, index=days)

    monkeypatch.setattr(service, "equal_weight_benchmark", benchmark)
    monkeypatch.setattr(service, "factor_attribution", lambda *a: {"capm": {}, "warnings": []})
    result = service.compare()
    assert result["period"] == dict(start="2025-01-06", end="2025-01-08", sessions=3)
    assert result["rows"][0]["index_exposures"][0]["mean_weight"] == pytest.approx(0.2)
    assert result["rows"][0]["total_return"] == pytest.approx(1.01**3 - 1)
