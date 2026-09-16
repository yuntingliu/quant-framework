from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from alphalab.analytics import TechnicalMetadata, technical_evidence
from alphalab.sdk.v1 import factor
from alphalab.strategy import get_factor_template

META = TechnicalMetadata("TEST", "NYSE", "USD", "unadjusted", "shares", "synthetic test")


def bars(count=280):
    close = np.arange(count, dtype=float) + 100
    return pd.DataFrame({"date": pd.bdate_range("2020-01-01", periods=count),
                         "open": close - 0.5, "high": close + 1, "low": close - 1,
                         "close": close, "volume": 1000.0})


def report(frame, **kwargs):
    return technical_evidence(frame, as_of=str(frame.date.iloc[-1].date()), metadata=META, **kwargs)


def test_known_linear_series_and_indicator_identities():
    frame = bars()
    result = report(frame)
    value = result["indicators"]
    assert value["ma_20"] == pytest.approx(369.5)
    assert value["return_5d"] == pytest.approx(379 / 374 - 1)
    assert value["rsi_14"] == 100
    assert value["true_range"] == value["atr_14"] == 2
    assert value["boll_middle"] == value["ma_20"]
    assert value["macd_histogram"] == pytest.approx(2 * (value["macd_diff"] - value["macd_dea"]))
    assert value["kdj_j"] == pytest.approx(3 * value["kdj_k"] - 2 * value["kdj_d"])
    assert value["obv"] == 279000
    json.dumps(result, allow_nan=False)


def test_gap_true_range_is_not_atr_and_future_is_excluded():
    frame = bars(30)
    cutoff = str(frame.date.iloc[-1].date())
    frame.loc[29, ["open", "high", "low", "close"]] = [180, 200, 175, 190]
    original = report(frame)
    assert original["indicators"]["true_range"] == 72
    assert original["indicators"]["atr_14"] == pytest.approx((13 * 2 + 72) / 14)
    future = pd.DataFrame({"date": [frame.date.iloc[-1] + pd.Timedelta(days=3)],
                           "open": [9999], "high": [10000], "low": [9998], "close": [9999], "volume": [1e12]})
    combined = pd.concat([frame, future], ignore_index=True)
    assert technical_evidence(combined, as_of=cutoff, metadata=META) == original


def test_flat_price_and_short_history_remain_honest():
    frame = bars(26)
    frame[["open", "high", "low", "close"]] = 100.0
    value = report(frame)["indicators"]
    assert value["rsi_14"] == 50
    assert value["atr_14"] == 0
    assert value["cci_20"] is None
    assert value["lower_shadow_fraction"] is None
    assert value["ma_60"] is None
    assert value["macd_dea"] is None
    assert value["kdj_k"] == value["kdj_d"] == 50
    assert value["obv"] == 0


@pytest.mark.parametrize("problem", ["duplicate", "missing", "negative_volume", "bounds", "intraday"])
def test_invalid_inputs_are_rejected(problem):
    frame = bars()
    if problem == "duplicate":
        frame = pd.concat([frame, frame.iloc[-1:]])
    elif problem == "missing":
        frame.loc[0, "close"] = np.nan
    elif problem == "negative_volume":
        frame.loc[0, "volume"] = -1
    elif problem == "bounds":
        frame.loc[0, "low"] = 1000
    else:
        frame.loc[0, "date"] += pd.Timedelta(hours=1)
    with pytest.raises(ValueError):
        report(frame)


def test_benchmark_compares_identical_dates_without_forward_fill():
    frame = bars(30)
    benchmark = frame[["date", "close"]].copy()
    benchmark.close = 100.0
    result = report(frame, benchmark=benchmark, benchmark_metadata=META)
    pair = result["benchmark"]["comparisons"]["5d"]
    assert pair["benchmark_return"] == 0
    assert pair["excess_percentage_points"] == pytest.approx(100 * (129 / 124 - 1))
    missing = benchmark.iloc[:-1]
    pair = report(frame, benchmark=missing, benchmark_metadata=META)["benchmark"]["comparisons"]["5d"]
    assert pair["benchmark_return"] is None
    assert pair["excess_percentage_points"] is None


def evaluate_template(name, frame):
    namespace = {"factor": factor}
    exec(get_factor_template(name).source, namespace)
    context = SimpleNamespace(history=lambda field, window: pd.DataFrame({"TEST": frame[field].tail(window).to_numpy()}))
    return namespace[name](context).iloc[0]


@pytest.mark.parametrize("name", ["lower_shadow_recovery", "three_white_soldiers", "volume_confirmed_breakout"])
def test_sdk_patterns_have_positive_negative_and_missing_cases(name):
    frame = bars(30)
    assert np.isnan(evaluate_template(name, frame.iloc[:0]))
    if name == "lower_shadow_recovery":
        frame.loc[29, ["open", "high", "low", "close"]] = [128, 130, 120, 129]
        pattern = "lower_shadow_recovery"
    elif name == "three_white_soldiers":
        frame.loc[27:29, ["open", "high", "low", "close"]] = [[100, 104, 99, 104], [103, 107, 102, 107], [106, 110, 105, 110]]
        pattern = "three_white_soldiers_proxy"
    else:
        frame.loc[29, ["open", "high", "low", "close", "volume"]] = [128, 134, 127, 133, 1500]
        pattern = "breakout_with_volume"
    assert evaluate_template(name, frame) == 1
    assert report(frame)["patterns"][pattern] is True
    negative = frame.copy()
    negative.loc[29, ["open", "high", "low", "close", "volume"]] = [130, 131, 119, 120, 500]
    assert evaluate_template(name, negative) == 0
    frame.loc[29, "close"] = np.nan
    assert np.isnan(evaluate_template(name, frame))
