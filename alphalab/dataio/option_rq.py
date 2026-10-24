"""Optional RQData adapter. Uses an already initialized client; never reads .env."""

import pandas as pd


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
