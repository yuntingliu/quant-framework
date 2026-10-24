"""Audit a daily close reconstruction against an independently dated trade grid."""

import json

import numpy as np
import pandas as pd


def reconstructed_close(row, evidence):
    """Verify the closing reference and an independently dated contract rescale."""
    frame = pd.DataFrame(evidence["bars"])
    columns = ["open", "high", "low", "close", "volume"]
    if not {"timestamp", *columns}.issubset(frame):
        raise ValueError("Incomplete close reconstruction evidence")
    timestamps = pd.to_datetime(frame.timestamp, errors="raise")
    day = pd.Timestamp(row["date"]).normalize()
    expected = pd.date_range(
        day + pd.Timedelta(hours=9, minutes=35), periods=24, freq="5min"
    ).union(pd.date_range(day + pd.Timedelta(hours=13, minutes=5), periods=24, freq="5min"))
    if len(frame) != 48 or timestamps.duplicated().any() or set(timestamps) != set(expected):
        raise ValueError("Close reconstruction requires the complete same-day 48-bar grid")
    frame = frame.assign(timestamp=timestamps).sort_values("timestamp")
    values = frame[columns].to_numpy(float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Invalid close reconstruction trade prices")
    traded = frame.loc[frame.volume.gt(0)]
    # RQ minute opens may carry the preceding reference, and a zero-volume
    # 15:00 bar can contain the exchange closing reference rather than last trade.
    # Do not invent daily OHL from those fields or label the reference a fill.
    close = float(frame.close.iloc[-1])
    if (
        traded.empty
        or close <= 0
        or not row["low"] - 1e-10 <= close <= row["high"] + 1e-10
        or not traded.close.between(row["low"] - 1e-10, row["high"] + 1e-10).all()
        or not np.isclose(frame.volume.sum(), row["volume"], rtol=0, atol=1e-8)
    ):
        raise ValueError("Intraday evidence does not reconcile daily volume and price bounds")
    following = evidence["next_property"]
    ratio = float(following["contract_multiplier"]) / float(row["contract_multiplier"])
    next_day = pd.Timestamp(following["date"])
    if (
        not day < next_day <= day + pd.Timedelta(days=30)
        or not np.isfinite(ratio)
        or ratio <= 0
        or abs(ratio - 1) < 1e-8
    ):
        raise ValueError("Close recovery requires a dated contract adjustment")
    if abs(float(following["strike_price"]) * ratio - row["strike_price"]) > 0.001 * ratio + 1e-8:
        raise ValueError("Contract strike and multiplier adjustment disagree")
    # Both original and closing references are rounded to the 0.0001 tick.
    if (
        abs(float(row.get("source_close", row["close"])) * ratio - close)
        > 0.00005 * (ratio + 1) + 1e-8
    ):
        raise ValueError("Source close does not match the confirmed adjustment scale")
    return close


def validate_close_evidence(frame):
    """Revalidate persisted evidence; recovery metadata cannot bypass quote checks."""
    if "close_recovery" not in frame:
        return 0
    recovered = frame.loc[frame.close_recovery.notna()]
    for row in recovered.to_dict("records"):
        if row["close_recovery"] != "rq_5m_adjustment_verified_reference":
            raise ValueError("Unknown close recovery method")
        if not np.isfinite(row["source_close"]) or row["low"] <= row["source_close"] <= row["high"]:
            raise ValueError("Close recovery needs the original inconsistent source close")
        close = reconstructed_close(row, json.loads(row["close_evidence"]))
        if not np.isclose(close, row["close"], rtol=0, atol=1e-10):
            raise ValueError("Recovered close disagrees with retained trade evidence")
    return len(recovered)
