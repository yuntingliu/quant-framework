"""Canonical monthly factor-return construction shared by runtime workflows."""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_factor_returns(
    bars: pd.DataFrame,
    fundamentals: pd.DataFrame,
    risk_free: pd.DataFrame | pd.Series,
) -> pd.DataFrame:
    """Build the barebone MKT/SMB/HML/MOM/RMW/rf monthly dataset.

    Characteristics are observed at the prior month end and returns are from
    the following month. Fundamentals are selected using their available date.
    """

    if bars.empty or fundamentals.empty:
        return _empty()
    required_bars = {"date", "symbol", "close", "raw_close"}
    required_fundamentals = {
        "available_date",
        "symbol",
        "shares",
        "bp",
        "roe",
    }
    missing = sorted(
        (required_bars - set(bars)) | (required_fundamentals - set(fundamentals))
    )
    if missing:
        raise ValueError(f"Factor construction is missing columns: {missing}")

    price_rows = bars.copy()
    price_rows["date"] = pd.to_datetime(price_rows["date"], errors="coerce")
    price_rows["symbol"] = price_rows["symbol"].astype(str).str.upper()
    adjusted = price_rows.pivot_table(
        index="date",
        columns="symbol",
        values="close",
        aggfunc="last",
    ).sort_index()
    raw = price_rows.pivot_table(
        index="date",
        columns="symbol",
        values="raw_close",
        aggfunc="last",
    ).sort_index()
    adjusted_monthly = adjusted.resample(pd.offsets.MonthEnd()).last()
    raw_monthly = raw.resample(pd.offsets.MonthEnd()).last()
    monthly_returns = adjusted_monthly.pct_change(fill_method=None)

    pit = fundamentals.copy()
    pit["available_date"] = pd.to_datetime(pit["available_date"], errors="coerce")
    pit["symbol"] = pit["symbol"].astype(str).str.upper()
    pit = pit.dropna(subset=["available_date", "symbol"]).sort_values(
        ["symbol", "available_date", "quarter"]
    )

    rows: list[dict] = []
    for position in range(1, len(monthly_returns.index)):
        date = monthly_returns.index[position]
        signal_date = monthly_returns.index[position - 1]
        returns = monthly_returns.loc[date].replace([np.inf, -np.inf], np.nan)
        latest = _latest_fundamentals(pit, signal_date)
        if latest.empty:
            continue
        shares = pd.to_numeric(latest["shares"], errors="coerce").reindex(
            raw_monthly.columns
        )
        market_cap = raw_monthly.loc[signal_date] * shares
        momentum = pd.Series(dtype=float)
        if position >= 12:
            momentum = (
                adjusted_monthly.iloc[position - 1]
                / adjusted_monthly.iloc[position - 12]
                - 1.0
            )
        rows.append(
            {
                "date": date,
                "MKT": float(returns.mean()),
                "SMB": _spread(market_cap, returns, high_minus_low=False),
                "HML": _spread(latest["bp"], returns),
                "MOM": _spread(momentum, returns),
                "RMW": _spread(latest["roe"], returns),
            }
        )
    if not rows:
        return _empty()
    output = pd.DataFrame(rows).set_index("date").sort_index()
    rf = _risk_free_series(risk_free)
    output["rf"] = rf.reindex(output.index, method="ffill").fillna(0.0)
    return output.reset_index()


def _latest_fundamentals(
    fundamentals: pd.DataFrame,
    asof: pd.Timestamp,
) -> pd.DataFrame:
    return (
        fundamentals.loc[fundamentals["available_date"].le(asof)]
        .sort_values(["symbol", "available_date", "quarter"])
        .groupby("symbol", as_index=False)
        .tail(1)
        .set_index("symbol")
    )


def _spread(
    values: pd.Series,
    returns: pd.Series,
    *,
    high_minus_low: bool = True,
) -> float:
    aligned = pd.concat(
        [
            pd.to_numeric(values, errors="coerce").rename("value"),
            pd.to_numeric(returns, errors="coerce").rename("return"),
        ],
        axis=1,
    ).dropna()
    if len(aligned) < 30:
        return float("nan")
    size = max(10, int(len(aligned) * 0.3))
    low = aligned.nsmallest(size, "value")["return"].mean()
    high = aligned.nlargest(size, "value")["return"].mean()
    return float(high - low if high_minus_low else low - high)


def _risk_free_series(values: pd.DataFrame | pd.Series) -> pd.Series:
    if isinstance(values, pd.Series):
        result = values.copy()
    elif values.empty:
        return pd.Series(dtype=float, name="rf")
    else:
        frame = values.copy()
        if "date" in frame:
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame = frame.set_index("date")
        column = "rf" if "rf" in frame else frame.columns[0]
        result = frame[column]
    result.index = pd.to_datetime(result.index, errors="coerce")
    return pd.to_numeric(result, errors="coerce").dropna().sort_index().rename("rf")


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "MKT", "SMB", "HML", "MOM", "RMW", "rf"])


__all__ = ["build_factor_returns"]
