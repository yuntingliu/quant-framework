"""Optional RQData adapter. Uses an already initialized client; never reads .env."""

import json

import pandas as pd

from alphalab.options.quote_evidence import reconstructed_close


def recover_daily_closes(bars, raw_intraday, next_properties, properties=None):
    """Preserve original anomalous closes and complete independent trade evidence."""
    intraday = raw_intraday.reset_index().rename(columns={"datetime": "timestamp"})
    if not {"timestamp", "order_book_id"}.issubset(intraday):
        raise ValueError("Missing intraday close evidence")
    frame = bars.copy()
    for index, row in frame.iterrows():
        if row.volume <= 0 or row.low - 1e-10 <= row.close <= row.high + 1e-10:
            continue
        minute = intraday.loc[intraday.order_book_id.astype(str).eq(str(row.order_book_id))].copy()
        minute = minute[["timestamp", "open", "high", "low", "close", "volume"]]
        minute["timestamp"] = pd.to_datetime(minute.timestamp).dt.strftime("%Y-%m-%dT%H:%M:%S")
        following = next_properties.loc[
            next_properties.order_book_id.astype(str).eq(str(row.order_book_id))
            & next_properties.date.gt(pd.Timestamp(row.date))
        ].sort_values("date")
        if following.empty:
            raise ValueError("Missing dated contract adjustment evidence")
        future = following.iloc[0]
        evidence = {
            "bars": minute.to_dict("records"),
            "next_property": {
                "date": str(pd.Timestamp(future.date).date()),
                "contract_multiplier": float(future.contract_multiplier),
                "strike_price": float(future.strike_price),
            },
        }
        current = row.to_dict()
        if properties is not None:
            match = properties.loc[
                properties.order_book_id.astype(str).eq(str(row.order_book_id))
                & properties.date.eq(pd.Timestamp(row.date))
            ]
            if len(match) != 1:
                raise ValueError("Missing same-day contract adjustment evidence")
            current.update(match.iloc[0][["strike_price", "contract_multiplier"]].to_dict())
        close = reconstructed_close(current, evidence)
        frame.loc[index, "source_close"] = float(row.close)
        frame.loc[index, "close_recovery"] = "rq_5m_adjustment_verified_reference"
        frame.loc[index, "close_evidence"] = json.dumps(evidence, allow_nan=False)
        frame.loc[index, "close"] = close
    return frame


def normalize(raw):
    if raw is None:
        return pd.DataFrame()
    frame = raw.copy()
    if any(name is not None for name in frame.index.names):
        frame = frame.reset_index()
    frame = frame.rename(columns={"trading_date": "date", "datetime": "date"})
    if "order_book_id" in frame:
        frame["order_book_id"] = frame.order_book_id.astype(str)
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame.date).dt.normalize()
    return frame
