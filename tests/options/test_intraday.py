"""Complete exchange grids, adjustment timing and SDK close availability."""

import numpy as np
import pandas as pd
import pytest

from alphalab.dataio.option_intraday import intraday_variance, normalize_intraday
from alphalab.sdk.v1 import Event, FactorContext


def five_minute_frame():
    morning = pd.date_range("2025-01-06 09:35", "2025-01-06 11:30", freq="5min")
    afternoon = pd.date_range("2025-01-06 13:05", "2025-01-06 15:00", freq="5min")
    return pd.DataFrame(
        {
            "datetime": morning.append(afternoon),
            "order_book_id": "510050.XSHG",
            "open": 3 * np.exp(np.arange(48) * 0.001),
            "close": 3 * np.exp(np.arange(1, 49) * 0.001),
        }
    ).set_index(["order_book_id", "datetime"])


def test_intraday_variance_matches_squared_returns_and_rejects_missing_bar():
    frame = normalize_intraday(five_minute_frame())
    history = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-03", "2025-01-06"]),
            "symbol": "510050.XSHG",
            "raw_close": [3, 3 * float(np.exp(0.048))],
            "adjusted_close": [3, 3 * float(np.exp(0.048))],
        }
    )
    daily = intraday_variance(frame, history).iloc[0]
    assert daily.intraday_variance == pytest.approx(48 * 0.001**2)
    assert daily.overnight_return == pytest.approx(0)
    assert daily.realized_variance == pytest.approx(48 * 0.001**2)
    with pytest.raises(ValueError, match="48"):
        normalize_intraday(five_minute_frame().iloc[:-1])


def test_distribution_adjustment_does_not_invent_overnight_risk():
    raw = five_minute_frame()
    raw[["open", "close"]] = 2.94
    frame = normalize_intraday(raw)
    history = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-03", "2025-01-06"]),
            "symbol": "510050.XSHG",
            "raw_close": [3, 2.94],
            "adjusted_close": [2.94, 2.94],
        }
    )
    assert intraday_variance(frame, history).realized_variance.iloc[0] == pytest.approx(0)


def context(event, as_of):
    return FactorContext(
        event=event,
        as_of=as_of,
        sessions=pd.bdate_range("2025-01-03", "2025-01-08"),
        symbols=["510050.SH"],
        bars=pd.DataFrame(
            {"date": [pd.Timestamp("2025-01-03")], "symbol": ["510050.SH"], "close": [3]}
        ),
        daily_factors=pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-01-06", "2025-01-08"]),
                "symbol": "510050.SH",
                "field": "option_smirk",
                "value": [0.1, 99],
            }
        ),
    )


def test_sdk_option_fields_are_close_only_and_never_carry_missing_day():
    assert context(Event.SESSION_CLOSE, "2025-01-06").daily_factor("option_smirk").iloc[0] == 0.1
    assert context(Event.SESSION_OPEN, "2025-01-07").daily_factor("option_smirk").iloc[0] == 0.1
    assert pd.isna(context(Event.SESSION_CLOSE, "2025-01-07").daily_factor("option_smirk").iloc[0])
    assert pd.isna(context(Event.SESSION_OPEN, "2025-01-08").daily_factor("option_smirk").iloc[0])
