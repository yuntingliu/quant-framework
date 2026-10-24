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


def test_intraday_recipe_respects_listing_and_recomputes_retained_variance(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from alphalab.data_sdk.v1 import DataRecipeContext, rq
    from alphalab.dataio.option_intraday import render_intraday_recipe
    from alphalab.dataio.runtime import RuntimeStore

    listings = {"510050.XSHG": "2025-01-06", "510300.XSHG": "2025-01-07"}
    store = RuntimeStore(tmp_path)
    history = pd.DataFrame(
        [
            {
                "date": day,
                "symbol": symbol,
                "raw_close": 3.0,
                "adjusted_close": 3.0,
                "source": "synthetic",
            }
            for symbol, listed in listings.items()
            for day in pd.bdate_range(listed, "2025-01-08")
        ]
    )
    store.write("rq.option_underlyings", history)

    class Vendor:
        def instruments(self, symbols):
            return [SimpleNamespace(order_book_id=s, listed_date=listings[s]) for s in symbols]

        def get_trading_dates(self, start, end, **kwargs):
            return pd.bdate_range(start, end)

        def get_price(self, symbols, **kwargs):
            records = []
            for symbol in symbols:
                for day in pd.bdate_range(kwargs["start_date"], kwargs["end_date"]):
                    if day < pd.Timestamp(listings[symbol]):
                        continue
                    times = pd.date_range(
                        day + pd.Timedelta(hours=9, minutes=35), periods=24, freq="5min"
                    ).append(
                        pd.date_range(
                            day + pd.Timedelta(hours=13, minutes=5), periods=24, freq="5min"
                        )
                    )
                    records.extend(
                        {"order_book_id": symbol, "datetime": stamp, "open": 3.0, "close": 3.0}
                        for stamp in times
                    )
            return pd.DataFrame(records).set_index(["order_book_id", "datetime"])

    monkeypatch.setattr(rq, "_connected_module", Vendor())
    source = render_intraday_recipe(start="2025-01-06", end="2025-01-08", symbols=tuple(listings))
    scope = {}
    exec(source, scope)
    run = DataRecipeContext(mode="run", root=str(tmp_path))
    scope["etf_intraday"](run)
    run.finalize()
    assert len(store.read("rq.option_etf_5m")) == 5 * 48
    # A correction outside the next requested date must update its old overnight
    # variance too; preserving a stale derived value would hide the correction.
    history.loc[
        history.symbol.eq("510050.XSHG") & history.date.eq(pd.Timestamp("2025-01-06")),
        "adjusted_close",
    ] = 2.9
    store.write("rq.option_underlyings", history)
    scope["etf_intraday"](
        DataRecipeContext(mode="run", root=str(tmp_path)), start="2025-01-08", end="2025-01-08"
    )
    derived = store.read("research.option_intraday_variance")
    corrected = derived.loc[
        derived.symbol.eq("510050.XSHG") & derived.date.eq(pd.Timestamp("2025-01-07"))
    ]
    assert corrected.realized_variance.iloc[0] == pytest.approx(np.log(3 / 2.9) ** 2)
