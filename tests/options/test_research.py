"""Payoff identities, label timing, embargo and future-mutation invariants."""

from math import exp

import numpy as np
import pandas as pd
import pytest

from alphalab.options import (
    EuropeanOption,
    black_scholes,
    research_panel,
    strip_moments,
    walk_forward_research,
)
from alphalab.options.features import _at_maturity


def test_strip_recovers_lognormal_log_moments_without_tail_extrapolation():
    forward = 100 * exp(0.03)
    rows = []
    for k in np.linspace(-1.1, 1.1, 1001):
        strike = forward * exp(float(k))
        for kind in ("C", "P"):
            rows.append(
                {
                    "maturity": 1,
                    "forward": forward,
                    "rate": 0.03,
                    "strike": strike,
                    "option_type": kind,
                    "volume": 100,
                    "multiplier": 10000,
                    "market_price": black_scholes(EuropeanOption(100, strike, 1, 0.03, 0.2, kind)),
                }
            )
    result = strip_moments(rows)
    assert result["raw1"] == pytest.approx(-0.02, abs=2e-5)
    assert result["raw2"] - result["raw1"] ** 2 == pytest.approx(0.04, abs=2e-5)
    assert result["q_skew"] == pytest.approx(0, abs=0.002)
    assert result["q_kurt"] == pytest.approx(3, abs=0.004)
    assert result["mf_variance"] == pytest.approx(0.04, abs=0.0001)
    assert result["truncated"]
    assert strip_moments(rows[:8]) == {}


def test_constant_maturity_interpolates_total_variance_and_refuses_extrapolation():
    nodes = [{"maturity": 20 / 365, "v": 0.04}, {"maturity": 40 / 365, "v": 0.09}]
    assert _at_maturity(nodes, 30, "v", variance=True) == pytest.approx(
        (20 * 0.04 + 40 * 0.09) / 60
    )
    assert _at_maturity(nodes, 10, "v", variance=True) is None
    assert _at_maturity(nodes, 50, "v", variance=True) is None


def synthetic_panel(days=260):
    dates = pd.bdate_range("2023-01-02", periods=days)
    rng = np.random.default_rng(57)
    features, bars = [], []
    for symbol in ("A", "B", "C", "D", "E", "F"):
        prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, days)))
        for index, day in enumerate(dates):
            features.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "option_smirk": rng.normal(0, 0.03),
                    "option_rv20": 0.2 + rng.normal(0, 0.01),
                    "option_momentum20": rng.normal(0, 0.02),
                }
            )
            bars.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "open": prices[index] * 1.001,
                    "close": prices[index],
                }
            )
    return pd.DataFrame(features), pd.DataFrame(bars)


def test_labels_start_at_next_open_and_retain_unknown_tail():
    features, bars = synthetic_panel(30)
    panel = research_panel(features, bars, horizon=5)
    sample = panel.loc[panel.symbol.eq("A")].sort_values("date")
    prices = bars.loc[bars.symbol.eq("A")].sort_values("date")
    assert sample.target_return.iloc[0] == pytest.approx(
        prices.close.iloc[5] / prices.open.iloc[1] - 1
    )
    assert sample.label_start.iloc[0] == prices.date.iloc[1]
    assert sample.label_end.iloc[0] == prices.date.iloc[5]
    assert sample.target_return.tail(5).isna().all()


def test_walk_forward_cannot_train_on_unfinished_labels_or_future_mutations():
    features, bars = synthetic_panel()
    panel = research_panel(features, bars, horizon=5)
    settings = {"fields": ["option_smirk"], "min_train": 60, "horizon": 5, "refit_every": 20}
    first = walk_forward_research(panel, **settings)
    assert first["coverage"]["unscored_rows"] == 30
    assert all(f["training_label_end"] < f["test_start"] for f in first["folds"])
    before = first["folds"][2]["test_end"]
    changed = panel.copy()
    changed.loc[changed.date.gt(pd.Timestamp(before)), "option_smirk"] = 100
    changed.loc[changed.label_end.gt(pd.Timestamp(before)), "target_return"] = -0.8
    second = walk_forward_research(changed, **settings)
    left = [
        (r["date"], r["symbol"], r["prediction"])
        for r in first["predictions"]
        if r["date"] <= before
    ]
    right = [
        (r["date"], r["symbol"], r["prediction"])
        for r in second["predictions"]
        if r["date"] <= before
    ]
    assert left == right
    assert first == walk_forward_research(panel, **settings)
    assert len(first["groups"]) == 1
    assert first["groups"][0]["high_minus_low"]["hac_t"] is not None


def test_research_bounds_and_missing_features_do_not_silently_change_model():
    features, bars = synthetic_panel(100)
    panel = research_panel(features, bars)
    with pytest.raises(ValueError, match="Insufficient"):
        walk_forward_research(panel, fields=["option_smirk"], min_train=120)
    with pytest.raises(ValueError, match="registered"):
        walk_forward_research(panel, fields=["future_return"])
    panel["option_smirk"] = np.nan
    with pytest.raises(ValueError, match="Insufficient"):
        walk_forward_research(panel, fields=["option_smirk"], min_train=60)


def test_selecting_a_control_as_predictor_does_not_invent_incremental_r_squared():
    features, bars = synthetic_panel()
    report = walk_forward_research(
        research_panel(features, bars), fields=["option_rv20"], min_train=60, controls=True
    )
    assert report["config"]["controls"] == ["option_rv20", "option_momentum20"]
    assert report["oos_r2_vs_controls"] == pytest.approx(0, abs=1e-12)
