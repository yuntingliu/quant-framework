"""Numerical identities and failure/recovery contracts for the optional extension."""

import json
from dataclasses import replace
from math import exp, log, sqrt

import numpy as np
import pandas as pd
import pytest
from scipy.special import ndtr
from scipy.stats import poisson

from alphalab.options import (
    EuropeanOption,
    QualityError,
    analyze_chain,
    black_scholes,
    greeks,
    implied_volatility,
    monte_carlo,
    stress_grid,
    validate_snapshot,
)

UNDERLYING = "510050.XSHG"


def snapshot(day="2025-01-06"):
    rows = []
    for kind, code in [("C", "10000001"), ("P", "10000002")]:
        option = EuropeanOption(3, 3, 30 / 365, 0.02, 0.25, kind, 0.01)
        price = black_scholes(option)
        rows.append(
            {
                "date": pd.Timestamp(day),
                "order_book_id": code,
                "underlying": UNDERLYING,
                "option_type": kind,
                "exercise_type": "E",
                "listed_date": "2024-12-01",
                "maturity_date": str((pd.Timestamp(day) + pd.Timedelta(days=30)).date()),
                "open": price,
                "high": price + 0.01,
                "low": max(price - 0.01, 0),
                "close": price,
                "settlement": price,
                "volume": 100.0,
                "open_interest": 200.0,
                "strike_price": 3.0,
                "contract_multiplier": 10000.0,
                "spot": 3.0,
            }
        )
    return pd.DataFrame(rows)


@pytest.mark.parametrize("kind", ["C", "P"])
def test_bs_benchmark_parity_iv_and_greeks(kind):
    option = EuropeanOption(100, 100, 1, 0.05, 0.2, kind)
    assert black_scholes(option) == pytest.approx(
        10.450583572185565 if kind == "C" else 5.573526022256971
    )
    assert implied_volatility(black_scholes(option), option) == pytest.approx(0.2, abs=1e-10)
    h = 0.01
    values = greeks(option)
    up = black_scholes(replace(option, spot=100 + h))
    down = black_scholes(replace(option, spot=100 - h))
    assert values["delta"] == pytest.approx((up - down) / (2 * h), abs=1e-07)
    assert values["gamma"] == pytest.approx(
        (up - 2 * black_scholes(option) + down) / h**2, abs=1e-07
    )
    for parameter, greek, scale in [
        ("volatility", "vega_1pct", 0.01),
        ("rate", "rho_1pct", 0.01),
        ("maturity", "theta_day", -1 / 365),
    ]:
        base = getattr(option, parameter)
        epsilon = 1e-05
        derivative = (
            black_scholes(replace(option, **{parameter: base + epsilon}))
            - black_scholes(replace(option, **{parameter: base - epsilon}))
        ) / (2 * epsilon)
        assert values[greek] == pytest.approx(derivative * scale, abs=1e-07)


def test_dividend_parity_and_deterministic_limits():
    call = EuropeanOption(3, 2.8, 0.5, 0.02, 0.4, "C", 0.03)
    put = replace(call, option_type="P")
    assert black_scholes(call) - black_scholes(put) == pytest.approx(
        3 * exp(-0.03 * 0.5) - 2.8 * exp(-0.02 * 0.5)
    )
    assert black_scholes(replace(call, maturity=0)) == pytest.approx(0.2)
    assert black_scholes(replace(put, maturity=0)) == 0
    assert black_scholes(replace(call, volatility=0)) == pytest.approx(
        max(3 * exp(-0.03 * 0.5) - 2.8 * exp(-0.02 * 0.5), 0)
    )


@pytest.mark.parametrize("price", [-1.0, 0.0, 3.0, float("nan"), float("inf")])
def test_iv_rejects_arbitrage_and_unidentifiable_boundaries(price):
    with pytest.raises(ValueError):
        implied_volatility(price, EuropeanOption(3, 3, 0.1, 0.02, 0.2, "P"))


def test_expiry_iv_and_bad_inputs():
    with pytest.raises(ValueError):
        implied_volatility(0.1, EuropeanOption(3, 3, 0, 0.02, 0.2))
    for changes in ({"spot": 0}, {"volatility": -1}, {"rate": float("nan")}, {"option_type": "A"}):
        with pytest.raises(ValueError):
            replace(EuropeanOption(3, 3, 0.1, 0.02, 0.2), **changes)


@pytest.mark.parametrize("kind", ["C", "P"])
def test_mc_gbm_and_jump_mixture_independent_benchmark(kind):
    option = EuropeanOption(3, 3, 0.5, 0.02, 0.25, kind, 0.01)
    gbm = monte_carlo(option, paths=200000, seed=7)
    assert abs(gbm["price"] - black_scholes(option)) < 4 * gbm["standard_error"]
    intensity, mu, delta = (1.3, -0.2, 0.18)
    mc = monte_carlo(
        option, paths=250000, seed=8, jump_intensity=intensity, jump_mean=mu, jump_volatility=delta
    )
    t = option.maturity
    kappa = exp(mu + delta**2 / 2) - 1
    exact = 0.0
    sign = 1 if kind == "C" else -1
    for n in range(50):
        m = (
            log(option.spot)
            + (option.rate - option.dividend_yield - intensity * kappa - option.volatility**2 / 2)
            * t
            + n * mu
        )
        variance = option.volatility**2 * t + n * delta**2
        d2 = (m - log(option.strike)) / sqrt(variance)
        d1 = d2 + sqrt(variance)
        conditional = sign * (
            exp(m + variance / 2) * ndtr(sign * d1) - option.strike * ndtr(sign * d2)
        )
        exact += poisson.pmf(n, intensity * t) * exp(-option.rate * t) * conditional
    assert abs(mc["price"] - exact) < 4 * mc["standard_error"]
    assert mc["discounted_spot_mean"] == pytest.approx(mc["discounted_spot_target"], abs=0.012)
    assert mc == monte_carlo(
        option, paths=250000, seed=8, jump_intensity=intensity, jump_mean=mu, jump_volatility=delta
    )


def test_rare_payoff_warning_and_stress_scaling():
    option = EuropeanOption(3, 30, 0.01, 0.02, 0.1)
    assert monte_carlo(option, paths=1000)["warning"] == "rare_payoff_insufficient_sampling"
    option = EuropeanOption(3, 3, 0.1, 0.02, 0.25, "P")
    result = stress_grid(
        option,
        spot_shocks=(0.0, -0.3),
        volatility_shifts=(0.0,),
        paths=10000,
        quantity=-2,
        multiplier=10220,
    )
    unchanged, crash = result["scenarios"]
    assert unchanged["bs_contract_pnl"] == 0
    assert unchanged["mc_contract_pnl"] == 0
    assert crash["bs_contract_pnl"] < 0
    assert crash["bs_contract_pnl"] == pytest.approx(
        (crash["bs_price"] - result["base_bs_price"]) * -20440
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("spot", np.nan),
        ("close", -0.1),
        ("contract_multiplier", 0),
        ("high", 0),
        ("date", pd.Timestamp("2025-01-07")),
        ("exercise_type", "A"),
        ("option_type", "X"),
    ],
)
def test_quality_rejects_invalid_data(field, value):
    frame = snapshot()
    frame.loc[0, field] = value
    with pytest.raises(QualityError):
        validate_snapshot(frame, ["10000001", "10000002"], "2025-01-06", UNDERLYING)


def test_quality_coverage_and_duplicates():
    frame = snapshot()
    with pytest.raises(QualityError):
        validate_snapshot(frame.iloc[:1], frame.order_book_id, "2025-01-06", UNDERLYING)
    with pytest.raises(QualityError):
        validate_snapshot(
            pd.concat([frame, frame.iloc[:1]]), frame.order_book_id, "2025-01-06", UNDERLYING
        )


def test_zero_volume_reference_close_is_flagged_and_never_inverted():
    frame = snapshot()
    frame.loc[0, "volume"] = 0
    frame.loc[0, "close"] = frame.loc[0, "low"] - 0.01
    quality = validate_snapshot(frame, frame.order_book_id, "2025-01-06", UNDERLYING)
    assert quality["zero_volume_close_outside_range"] == 1
    report = analyze_chain(frame, rate=0.02, dividend_yield=0.01, min_volume=0)
    assert report["contracts"][0]["status"] == "illiquid"
    assert report["contracts"][0]["iv"] is None
    assert report["contracts"][0]["quote_status"] == "no_trade_reference"
    frame.loc[0, "volume"] = 1
    with pytest.raises(QualityError, match="Traded close"):
        validate_snapshot(frame, frame.order_book_id, "2025-01-06", UNDERLYING)


def test_chain_is_standard_json_serializable():
    report = analyze_chain(snapshot(), rate=0.02, dividend_yield=0.01)
    restored = json.loads(json.dumps(report, allow_nan=False))
    assert restored["put_call_parity"][0]["flag"] is False
    assert restored["eligible"] == 2


def forward_snapshot():
    rows = []
    for days, q in [(30, 0.08), (90, 0.03)]:
        for strike in (2.9, 3.0, 3.1):
            for kind in ("C", "P"):
                row = snapshot().iloc[0].to_dict()
                price = black_scholes(EuropeanOption(3, strike, days / 365, 0.02, 0.25, kind, q))
                row.update(
                    order_book_id=f"{days}_{strike}_{kind}",
                    option_type=kind,
                    maturity_date=str(
                        (pd.Timestamp("2025-01-06") + pd.Timedelta(days=days)).date()
                    ),
                    strike_price=strike,
                    open=price,
                    high=price + 0.01,
                    low=max(0, price - 0.01),
                    close=price,
                    settlement=price,
                )
                rows.append(row)
    return pd.DataFrame(rows)


def test_put_call_forward_recovers_expiry_specific_carry_and_iv():
    report = analyze_chain(forward_snapshot(), rate=0.02, carry_mode="put_call_forward")
    assert report["eligible"] == 12
    assert [r["effective_dividend_yield"] for r in report["forward_curve"]] == pytest.approx(
        [0.08, 0.03]
    )
    assert all(row["iv"] == pytest.approx(0.25, abs=1e-10) for row in report["contracts"])
    assert all(row["pairs"] == 3 and row["status"] == "ready" for row in report["forward_curve"])
    json.dumps(report, allow_nan=False)


def test_put_call_forward_refuses_sparse_or_inconsistent_pairs():
    sparse = analyze_chain(snapshot(), rate=0.02, carry_mode="put_call_forward")
    assert sparse["eligible"] == 0
    assert sparse["forward_curve"][0]["status"] == "insufficient_pairs"
    assert all(row["status"] == "forward_unavailable" for row in sparse["contracts"])
    frame = forward_snapshot()
    frame.loc[0, ["open", "high", "low", "close", "settlement"]] = 0.5
    bad = analyze_chain(frame, rate=0.02, carry_mode="put_call_forward")
    assert bad["forward_curve"][0]["status"] == "inconsistent_pairs"


def test_carry_inputs_are_explicit_and_unambiguous():
    with pytest.raises(ValueError, match="explicit finite dividend"):
        analyze_chain(snapshot(), rate=0.02)
    with pytest.raises(ValueError, match="Omit dividend"):
        analyze_chain(snapshot(), rate=0.02, dividend_yield=0.01, carry_mode="put_call_forward")


def test_forward_pair_window_handles_coarse_strike_ladders_explicitly():
    frame = forward_snapshot()
    frame["strike_price"] = frame.strike_price.replace({2.9: 2.7, 3.1: 3.3})
    for index, row in frame.iterrows():
        t = (pd.Timestamp(row.maturity_date) - pd.Timestamp("2025-01-06")).days / 365
        q = 0.08 if t < 0.2 else 0.03
        price = black_scholes(
            EuropeanOption(3, row.strike_price, t, 0.02, 0.25, row.option_type, q)
        )
        frame.loc[index, ["open", "high", "low", "close", "settlement"]] = price
    report = analyze_chain(frame, rate=0.02, carry_mode="put_call_forward")
    assert report["eligible"] == 12
    assert all(f["expanded_pair_window"] and f["pairs"] == 3 for f in report["forward_curve"])


def test_no_stress_after_expiry_and_rare_ci_is_unavailable():
    option = EuropeanOption(3, 30, 0.01, 0.02, 0.1)
    assert monte_carlo(option, paths=1000)["ci95"] is None
    with pytest.raises(ValueError, match="expiry"):
        stress_grid(option, days_forward=5, paths=1000)


def test_chain_iv_liquidity_parity_and_multiplier_pairing():
    frame = snapshot()
    report = analyze_chain(frame, rate=0.02, dividend_yield=0.01)
    assert report["eligible"] == 2
    assert all((r["iv"] == pytest.approx(0.25) for r in report["contracts"]))
    assert report["put_call_parity"][0]["residual"] == pytest.approx(0, abs=1e-12)
    frame.loc[0, "volume"] = 0
    report = analyze_chain(frame, rate=0.02, dividend_yield=0.01)
    assert report["contracts"][0]["status"] == "illiquid"
    assert report["eligible"] == 1
    frame = snapshot()
    frame.loc[0, "contract_multiplier"] = 10220
    assert not analyze_chain(frame, rate=0.02, dividend_yield=0.01)["put_call_parity"]
