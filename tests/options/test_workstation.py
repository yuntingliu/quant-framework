"""Market-curve, surface and coherent basket-model invariants."""

from math import exp
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from alphalab.dataio.option_snapshots import normalize_option_snapshot
from alphalab.dataio.runtime import RuntimeStore
from alphalab.options import (
    DiscountCurve,
    EuropeanOption,
    analyze_chain,
    basket_stress,
    black_scholes,
    joint_monte_carlo,
    surface_iv,
    volatility_surface,
)
from alphalab.options.curves import normalize_yield_curve
from apps.api.services import options_service

DAY = "2025-01-06"
ETF = "510050.XSHG"


def market_snapshot(vols=(0.2, 0.22)):
    bars, properties, instruments = [], [], []
    for days, vol in zip((30, 90), vols):
        expiry = str((pd.Timestamp(DAY) + pd.Timedelta(days=days)).date())
        for index, k in enumerate(np.linspace(-0.12, 0.12, 9)):
            strike = 3 * exp(float(k))
            for kind in ("C", "P"):
                code = f"{days}{index}{kind}"
                value = black_scholes(EuropeanOption(3, strike, days / 365, 0.02, vol, kind, 0.01))
                bars.append(
                    {
                        "date": pd.Timestamp(DAY),
                        "order_book_id": code,
                        "open": value,
                        "high": value + 0.01,
                        "low": max(0, value - 0.01),
                        "close": value,
                        "settlement": value,
                        "volume": 100.0,
                        "open_interest": 300.0,
                    }
                )
                properties.append(
                    {
                        "date": pd.Timestamp(DAY),
                        "order_book_id": code,
                        "strike_price": strike,
                        "contract_multiplier": 10000.0,
                    }
                )
                instruments.append(
                    SimpleNamespace(
                        order_book_id=code,
                        underlying_order_book_id=ETF,
                        option_type=kind,
                        exercise_type="E",
                        listed_date="2024-12-01",
                        maturity_date=expiry,
                    )
                )
    spot = pd.DataFrame(
        {"date": [pd.Timestamp(DAY)], "order_book_id": [ETF], "close": [3.0], "volume": [100.0]}
    )
    return normalize_option_snapshot(
        pd.DataFrame(bars),
        pd.DataFrame(properties),
        instruments,
        spot,
        [r["order_book_id"] for r in bars],
        ETF,
        DAY,
    )


def rate_frame():
    raw = pd.DataFrame(
        {"0S": [np.expm1(0.02)], "1M": [np.expm1(0.02)], "1Y": [np.expm1(0.02)]},
        index=pd.DatetimeIndex([DAY], name="trading_date"),
    )
    return normalize_yield_curve(raw)


def test_curve_uses_annual_decimal_convention_and_no_future_nodes():
    curve = DiscountCurve.as_of(rate_frame(), DAY)
    assert curve.rate(0.3) == pytest.approx(0.02)
    assert curve.discount(0.3) == pytest.approx(exp(-0.02 * 0.3))
    with pytest.raises(ValueError, match="at or before"):
        DiscountCurve.as_of(rate_frame(), "2025-01-05")
    with pytest.raises(ValueError, match="stale"):
        DiscountCurve.as_of(rate_frame(), "2025-01-15")
    with pytest.raises(ValueError, match="decimal"):
        normalize_yield_curve(pd.DataFrame({"1M": [2.5]}, index=pd.to_datetime([DAY])))


def test_log_discount_interpolation_and_negative_yields():
    curve = DiscountCurve(DAY, (0.1, 1.0), (-0.01, 0.02))
    expected = np.interp(0.5, [0.1, 1.0], [-0.001, 0.02])
    assert curve.discount(0.5) == pytest.approx(exp(-expected))
    assert curve.discount(0.05) > 1


def test_svi_surface_round_trip_and_no_wing_extrapolation():
    chain = analyze_chain(market_snapshot(), rate=0.02, carry_mode="put_call_forward")
    surface = volatility_surface(chain)
    assert len(surface["fits"]) == 2
    assert surface["usable_for_interpolation"], surface["warnings"]
    assert surface["fits"][0]["atm_iv"] == pytest.approx(0.2, abs=0.002)
    assert surface["fits"][1]["atm_iv"] == pytest.approx(0.22, abs=0.002)
    assert 0.2 <= surface_iv(surface, 60 / 365, 0.0) <= 0.23
    with pytest.raises(ValueError, match="moneyness"):
        surface_iv(surface, 60 / 365, 2.0)


def test_calendar_arbitrage_is_reported_without_silent_repair():
    chain = analyze_chain(market_snapshot((0.5, 0.1)), rate=0.02, carry_mode="put_call_forward")
    surface = volatility_surface(chain)
    assert surface["calendar_violations"] > 0
    assert not surface["usable_for_interpolation"]
    with pytest.raises(ValueError, match="diagnostics"):
        surface_iv(surface, 60 / 365, 0.0)


def leg(kind="C", quantity=1.0, maturity=1.0):
    option = EuropeanOption(100, 100, maturity, 0.05, 0.2, kind, 0.01)
    return {
        "order_book_id": kind,
        "underlying": ETF,
        "status": "eligible",
        "spot": 100.0,
        "strike": 100.0,
        "maturity": maturity,
        "rate": 0.05,
        "iv": 0.2,
        "option_type": kind,
        "effective_dividend_yield": 0.01,
        "quantity": quantity,
        "multiplier": 1.0,
        "market_price": black_scholes(option),
    }


def asset():
    return {
        ETF: {
            "spot": 100.0,
            "volatility": 0.2,
            "forwards": [{"maturity": 1.0, "forward": 100 * exp(0.04)}],
        }
    }


def test_basket_full_revaluation_and_signed_cash_risk():
    result = basket_stress([leg("P", -2)], spot_shocks=(0.0, -0.3), volatility_shifts=(0.0,))
    assert result["scenarios"][0]["pnl"] == pytest.approx(0, abs=1e-10)
    assert result["scenarios"][1]["pnl"] < 0
    assert result["greeks"]["cash_delta"] > 0
    assert result["greeks"]["vega_1pct"] < 0
    with pytest.raises(ValueError, match="horizon"):
        basket_stress([leg()], days_forward=366)


def test_joint_gbm_matches_bs_and_put_call_shared_paths():
    call = joint_monte_carlo([leg()], asset(), paths=50000, jump_intensity=0)
    assert abs(call["value"] - leg()["market_price"]) < 4 * call["standard_error"]
    paired = joint_monte_carlo(
        [leg("C"), leg("P", -1)],
        asset(),
        paths=60000,
        jump_intensity=1.0,
        jump_mean=-0.2,
        jump_volatility=0.1,
    )
    expected = 100 * exp(-0.01) - 100 * exp(-0.05)
    assert abs(paired["value"] - expected) < 4 * paired["standard_error"]
    flat = joint_monte_carlo([leg("C"), leg("C", -1)], asset(), paths=1000)
    assert flat["value"] == 0 and flat["standard_error"] == 0


def test_joint_model_seed_and_negative_portfolio_values():
    first = joint_monte_carlo([leg("P", -2)], asset(), paths=10000, seed=8)
    assert first == joint_monte_carlo([leg("P", -2)], asset(), paths=10000, seed=8)
    assert first["value"] < 0 and first["ci95"][1] < 0


def test_workstation_uses_real_dated_curve_and_source_contracts(tmp_path):
    store = RuntimeStore(tmp_path)
    store.write("rq.option_snapshots", market_snapshot())
    store.write("rq.option_rates", rate_frame())
    report = options_service.analyze_snapshot(ETF, DAY, root=tmp_path)
    assert report["discount_curve"]["source"] == "RQ China government yield curve"
    assert all(row["rate"] == pytest.approx(0.02) for row in report["contracts"])
    selected = [r for r in report["contracts"] if r["status"] == "eligible"][0]
    result = options_service.portfolio(
        {
            "day": DAY,
            "legs": [{"underlying": ETF, "contract": selected["order_book_id"], "quantity": -1}],
            "paths": 2000,
            "include_mc": True,
        },
        root=tmp_path,
    )
    assert len(result["scenarios"]) == 33
    assert result["monte_carlo"]["base"]["measure"] == "Q"
    assert options_service.universe(tmp_path)["total_rows"] == 36


def test_feature_recipe_is_incremental_and_uses_existing_daily_factor_provider(tmp_path):
    from alphalab import create_runtime_engine
    from alphalab.data_sdk.v1 import DataRecipeContext
    from alphalab.dataio.option_features import iter_feature_batches

    store = RuntimeStore(tmp_path)
    store.write("rq.option_snapshots", market_snapshot())
    store.write("rq.option_rates", rate_frame())
    dates = pd.bdate_range(end=DAY, periods=30)
    store.write(
        "rq.option_underlyings",
        pd.DataFrame(
            {
                "date": dates,
                "symbol": ETF,
                "raw_close": 3.0,
                "adjusted_close": 3.0,
                "source": "synthetic_test",
            }
        ),
    )
    context = DataRecipeContext(mode="run", root=str(tmp_path))
    batches = list(iter_feature_batches(context, start=DAY, end=DAY, symbols=[ETF]))
    assert len(batches) == 1
    assert batches[0].option_atm_iv_30.iloc[0] == pytest.approx(0.2, abs=1e-8)
    assert pd.isna(batches[0].option_vrp_hf_30.iloc[0])
    context.publish("research.option_features", batches[0])
    assert list(iter_feature_batches(context, start=DAY, end=DAY, symbols=[ETF])) == []
    from alphalab.dataio.option_features import render_feature_recipe
    from alphalab.dataio.recipes import execute_data_recipe

    source = render_feature_recipe(start=DAY, end=DAY, symbols=[ETF])
    repeated = execute_data_recipe(source, mode="run", root=tmp_path)
    assert repeated["published"] == []
    assert repeated["outputs"][0]["name"] == "published_feature_rows"
    factors = create_runtime_engine(tmp_path).get_daily_factors(
        ["510050.SH"], ["option_atm_iv_30"], DAY, DAY
    )
    assert factors.value.iloc[0] == pytest.approx(0.2, abs=1e-8)


def test_quality_view_reuses_only_a_fresh_full_scope_check(tmp_path, monkeypatch):
    store = RuntimeStore(tmp_path)
    frame = market_snapshot()
    store.write("rq.option_snapshots", frame)
    store.write("rq.option_rates", rate_frame())
    store.write(
        "rq.option_underlyings",
        pd.DataFrame(
            {
                "date": [pd.Timestamp(DAY)],
                "symbol": [ETF],
                "raw_close": [3.0],
                "adjusted_close": [3.0],
                "source": ["synthetic_test"],
            }
        ),
    )
    first = options_service.quality(tmp_path)
    assert first["snapshots"]["status"] == "passed"
    # Quality timestamps use whole seconds; move the fixture's file mtime into
    # the checked interval without changing its content.
    import os

    for dataset in ("rq.option_snapshots", "rq.option_rates", "rq.option_underlyings"):
        checked = pd.Timestamp(store.operations.latest_quality(dataset)["checked_at"]).timestamp()
        for path in store.catalog.files(dataset):
            os.utime(path, (checked - 1, checked - 1))
    original, calls = options_service.validate_dataset, []

    def tracked(dataset, *args, **kwargs):
        calls.append(dataset)
        return original(dataset, *args, **kwargs)

    monkeypatch.setattr(options_service, "validate_dataset", tracked)
    assert options_service.quality(tmp_path)["snapshots"]["status"] == "passed" and not calls
    store.write("rq.option_snapshots", frame)
    options_service.quality(tmp_path)
    assert calls == ["rq.option_snapshots"]
