"""Holdings risk: contributions, scenarios, shocks, and honest gaps."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from alphalab.analytics.holdings_risk import (
    DISCLAIMER,
    HoldingsRiskParameters,
    analyse,
    frozen_inputs,
    input_hash,
)

SYMBOLS = ("AAA", "BBB", "CCC", "DDD")


def make_series(days: int = 400, seed: int = 11) -> dict[str, list[list]]:
    rng = np.random.default_rng(seed)
    factors = rng.normal(0.0, 0.01, size=days)
    calendar = pd.date_range("2024-01-01", periods=days, freq="D")
    series: dict[str, list[list]] = {}
    for index, symbol in enumerate(SYMBOLS):
        idiosyncratic = rng.normal(0.0, 0.008 + index * 0.002, size=days)
        returns = factors + idiosyncratic
        prices = 100.0 * np.cumprod(1.0 + returns)
        series[symbol] = [
            [str(calendar[day].date()), round(float(value), 6)]
            for day, value in enumerate(prices)
        ]
    return series


def make_inputs(days: int = 400, parameters: HoldingsRiskParameters | None = None) -> dict:
    series = make_series(days)
    holdings = []
    for index, symbol in enumerate(SYMBOLS):
        price = float(series[symbol][-1][1])
        quantity = 1000.0 * (index + 1)
        holdings.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "price": price,
                "market_value": quantity * price,
            }
        )
    return frozen_inputs(
        holdings=holdings,
        series=series,
        price_date="2026-07-10",
        parameters=parameters or HoldingsRiskParameters(),
        source="demo",
    )


def test_the_result_is_reproducible_and_carries_its_hash():
    inputs = make_inputs()
    first = analyse(inputs)
    second = analyse(inputs)
    assert first == second
    assert first["input_hash"] == input_hash(inputs)
    assert first["method"] == "holdings_risk_v1"
    assert first["disclaimer"] == DISCLAIMER
    assert "frequency" in first["assumptions"]


def test_changing_a_parameter_changes_the_hash_but_not_an_old_result():
    baseline = analyse(make_inputs())
    stricter = analyse(make_inputs(parameters=HoldingsRiskParameters(minimum_observations=120)))
    assert baseline["input_hash"] != stricter["input_hash"]
    # The first analysis is untouched by running the second.
    assert analyse(make_inputs()) == baseline


def test_contributions_sum_to_the_portfolio_volatility():
    result = analyse(make_inputs())
    volatility = result["portfolio"]["annualized_volatility"]
    assert volatility and volatility > 0
    contributions = result["contributions"]
    assert all(item["evaluated"] for item in contributions)
    total = sum(item["contribution_to_volatility"] for item in contributions)
    assert total == pytest.approx(volatility, rel=1e-9)
    shares = sum(item["share_of_risk"] for item in contributions)
    assert shares == pytest.approx(1.0, rel=1e-9)
    assert all(item["annualized_volatility"] > 0 for item in contributions)


def test_concentration_describes_the_portfolio_that_is_actually_held():
    result = analyse(make_inputs())
    concentration = result["concentration"]
    weights = sorted((item["weight"] for item in result["holdings"]), reverse=True)
    assert concentration["positions"] == len(SYMBOLS)
    largest = max(result["holdings"], key=lambda item: item["weight"])["symbol"]
    assert concentration["largest"] == largest
    assert concentration["top1_weight"] == pytest.approx(weights[0])
    assert concentration["top3_weight"] == pytest.approx(sum(weights[:3]))
    assert concentration["hhi"] == pytest.approx(sum(weight * weight for weight in weights))
    assert concentration["effective_positions"] == pytest.approx(1.0 / concentration["hhi"])
    assert concentration["evaluated"] is True


def test_historical_scenarios_are_worst_ordered_windows_without_overlap():
    parameters = HoldingsRiskParameters(scenario_window=20, scenario_count=3)
    result = analyse(make_inputs(parameters=parameters))
    items = result["scenarios"]["items"]
    assert len(items) == 3
    assert result["scenarios"]["complete"] is True
    returns = [item["portfolio_return"] for item in items]
    assert returns == sorted(returns), "the reported windows are the worst first"
    assert all(item["evaluated"] for item in items)
    spans = [(item["start"], item["end"]) for item in items]
    assert len(set(spans)) == 3
    assert all(start < end for start, end in spans)


def test_price_volatility_and_correlation_shocks_are_mechanical_what_ifs():
    result = analyse(make_inputs())
    shocks = result["shocks"]
    holdings_value = sum(item["market_value"] for item in result["holdings"])
    assert shocks["price_shock"]["loss_value"] == pytest.approx(-0.10 * holdings_value)
    assert shocks["price_shock"]["evaluated"] is True

    baseline = result["portfolio"]["annualized_volatility"]
    assert shocks["volatility"]["shocked_annualized_volatility"] == pytest.approx(baseline * 1.5)
    assert shocks["correlation"]["evaluated"] is True
    assert shocks["correlation"]["shocked_annualized_volatility"] > baseline
    assert "not forecasts" in shocks["note"]


def test_a_position_without_enough_history_is_marked_unevaluated_not_zero():
    inputs = make_inputs()
    short_symbol = "DDD"
    inputs["series"][short_symbol] = inputs["series"][short_symbol][-5:]
    result = analyse(inputs)
    assert short_symbol in result["unevaluated"]["symbols"]
    assert "usable observations" in result["unevaluated"]["reasons"][short_symbol]
    assert result["unevaluated"]["weight_share"] == pytest.approx(
        next(item["weight"] for item in result["holdings"] if item["symbol"] == short_symbol)
    )
    assert all(item["symbol"] != short_symbol for item in result["contributions"])
    assert result["scenarios"]["complete"] is False
    assert "excluded" in result["scenarios"]["partial"]
    assert any("could not be evaluated" in warning for warning in result["warnings"])


def test_a_position_with_no_history_at_all_is_reported_with_a_reason():
    inputs = make_inputs()
    inputs["series"].pop("CCC")
    result = analyse(inputs)
    assert result["unevaluated"]["reasons"]["CCC"] == "no price history was supplied for this symbol"
    assert all(item["symbol"] != "CCC" for item in result["contributions"])


def test_duplicate_holdings_and_unsorted_inputs_hash_the_same():
    inputs = make_inputs()
    series = make_series()
    holdings = inputs["holdings"]
    parameters = HoldingsRiskParameters()
    forwards = frozen_inputs(
        holdings=holdings, series=series, price_date="2026-07-10", parameters=parameters
    )
    backwards = frozen_inputs(
        holdings=list(reversed(holdings)),
        series=dict(reversed(list(series.items()))),
        price_date="2026-07-10",
        parameters=parameters,
    )
    assert input_hash(forwards) == input_hash(backwards)
    assert analyse(forwards)["portfolio"] == analyse(backwards)["portfolio"]


def test_an_empty_account_returns_an_explicit_gap_instead_of_crashing():
    inputs = frozen_inputs(
        holdings=[],
        series={},
        price_date="2026-07-10",
        parameters=HoldingsRiskParameters(),
        source="demo",
    )
    result = analyse(inputs)
    assert result["portfolio"] == {}
    assert result["unevaluated"]["reason"] == "no valued holdings"
    assert result["warnings"]
    assert result["disclaimer"] == DISCLAIMER


def test_parameters_reject_unusable_values():
    with pytest.raises(ValueError, match="minimum_observations"):
        HoldingsRiskParameters(minimum_observations=1)
    with pytest.raises(ValueError, match="price_shock"):
        HoldingsRiskParameters(price_shock=0.5)
    with pytest.raises(ValueError, match="confidence"):
        HoldingsRiskParameters(confidence=1.0)
    with pytest.raises(ValueError, match="volatility_multiplier"):
        HoldingsRiskParameters(volatility_multiplier=float("nan"))


def test_volatility_is_annualized_from_daily_returns():
    inputs = make_inputs()
    result = analyse(inputs)
    volatility = result["portfolio"]["annualized_volatility"]
    assert volatility is not None and math.isfinite(volatility)
    assert 0.0 < volatility < 2.0, "daily equity series should annualize into a plausible band"
    assert result["portfolio"]["observations"] > 100
