from __future__ import annotations

import numpy as np
import pandas as pd

from alphalab.analytics.factor_evidence import factor_research_report


def _synthetic_factor_evidence(periods: int = 31):
    sessions = pd.bdate_range("2020-01-02", periods=periods * 5 + 2)
    signal_dates = list(sessions[::5][:periods])
    entry_dates = [sessions[sessions.get_loc(date) + 1] for date in signal_dates]
    symbols = [f"S{index:02d}" for index in range(15)]
    scores = dict(zip(symbols, np.linspace(-1.0, 1.0, len(symbols)), strict=True))
    price_events: dict[pd.Timestamp, dict[str, float]] = {
        entry_dates[0]: {symbol: 100.0 for symbol in symbols}
    }
    levels = {symbol: 100.0 for symbol in symbols}
    for entry_date in entry_dates[1:]:
        levels = {
            symbol: value * (1.0 + scores[symbol] * 0.01)
            for symbol, value in levels.items()
        }
        price_events[entry_date] = dict(levels)
    rows = []
    active = {symbol: 100.0 for symbol in symbols}
    for date in sessions:
        if date in price_events:
            active = price_events[date]
        rows.extend(
            {"date": date, "symbol": symbol, "open": active[symbol]}
            for symbol in symbols
        )
    snapshots = [
        {
            "date": date.strftime("%Y-%m-%d"),
            "values": [
                {"symbol": symbol, "value": scores[symbol]} for symbol in symbols
            ],
        }
        for date in signal_dates
    ]
    return snapshots, pd.DataFrame(rows)


def test_factor_research_uses_next_open_and_chronological_holdout() -> None:
    snapshots, bars = _synthetic_factor_evidence()
    result = factor_research_report(
        snapshots,
        bars,
        frequency="weekly",
        quantiles=5,
        horizons=(1, 3, 6),
    )

    assert result["status"] == "sufficient"
    assert result["periods"] == 30
    assert result["summary"]["mean_rank_ic"] > 0.99
    assert result["summary"]["long_short"]["annual_return"] > 0
    assert result["decay"]["3"]["observations"] == 28
    assert set(result["summary"]["quantile_returns"]) == {"1", "2", "3", "4", "5"}
    assert result["stability"]["development"]["periods"] == 21
    assert result["stability"]["validation"]["periods"] == 9
    assert result["point_in_time_audit"]["forward_window_strictly_after_signal"] is True
    assert all(row["entry_date"] > row["signal_date"] for row in result["rows"])


def test_factor_research_reports_insufficient_evidence_without_zero_filling() -> None:
    snapshots, bars = _synthetic_factor_evidence(periods=10)
    first_symbol = snapshots[0]["values"][0]["symbol"]
    first_entry = pd.Timestamp(snapshots[0]["date"]) + pd.offsets.BDay(1)
    bars.loc[
        bars["date"].eq(first_entry) & bars["symbol"].eq(first_symbol), "open"
    ] = np.nan

    result = factor_research_report(
        snapshots,
        bars,
        frequency="weekly",
        quantiles=5,
        horizons=(1, 3, 6),
    )

    assert result["status"] == "insufficient"
    assert result["summary"]["average_coverage"] < 1.0
    assert any("Fewer than 24" in warning for warning in result["warnings"])


def test_factor_research_audit_surfaces_future_input_cutoffs() -> None:
    snapshots, bars = _synthetic_factor_evidence(periods=10)
    snapshots[0]["input_audit"] = {
        "factor_input_max_date": "2099-01-01",
        "security_snapshot_max_date": snapshots[0]["date"],
        "fundamental_available_max_date": None,
    }

    result = factor_research_report(
        snapshots,
        bars,
        frequency="weekly",
        quantiles=5,
        horizons=(1, 3, 6),
    )

    audit = result["point_in_time_audit"]
    assert audit["factor_observation_at_or_before_signal"] is False
    assert audit["input_cutoff_violations"][0]["field"] == "factor_input_max_date"
