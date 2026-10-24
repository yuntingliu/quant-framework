"""Five-minute ETF realized variance with complete sessions and overnight adjustment."""

import hashlib

import numpy as np
import pandas as pd

from .symbols import to_rq_symbol


def normalize_intraday(raw):
    frame = raw.reset_index().rename(columns={"datetime": "timestamp", "order_book_id": "symbol"})
    if "timestamp" not in frame or not {"symbol", "open", "close"}.issubset(frame):
        raise ValueError("ETF five-minute prices need timestamps, symbols, open and close")
    frame["timestamp"] = pd.to_datetime(frame.timestamp)
    frame["date"] = frame.timestamp.dt.normalize()
    frame["symbol"] = frame.symbol.map(to_rq_symbol)
    validate_intraday(frame)
    frame["source"] = "rqdata_5m_unadjusted"
    return frame.sort_values(["symbol", "timestamp"]).reset_index(drop=True)


def validate_intraday(frame):
    if frame.empty or frame.duplicated(["timestamp", "symbol"]).any():
        raise ValueError("Empty or duplicate ETF five-minute prices")
    if (
        not np.isfinite(frame[["open", "close"]].to_numpy(float)).all()
        or frame[["open", "close"]].le(0).any().any()
    ):
        raise ValueError("Invalid ETF five-minute prices")
    morning = pd.date_range("2000-01-01 09:35", "2000-01-01 11:30", freq="5min")
    afternoon = pd.date_range("2000-01-01 13:05", "2000-01-01 15:00", freq="5min")
    expected = set(morning.strftime("%H:%M")) | set(afternoon.strftime("%H:%M"))
    for _, group in frame.groupby(["date", "symbol"]):
        if (
            len(group) != 48
            or set(pd.to_datetime(group.timestamp).dt.strftime("%H:%M")) != expected
        ):
            raise ValueError("Incomplete five-minute ETF session; require all 48 exchange bars")


def intraday_variance(frame, adjusted_history):
    validate_intraday(frame)
    history = adjusted_history.sort_values(["symbol", "date"]).copy()
    history["previous_adjusted_close"] = history.groupby("symbol").adjusted_close.shift(1)
    lookup = history.set_index(["date", "symbol"])
    output = []
    for (day, symbol), group in frame.groupby(["date", "symbol"]):
        group = group.sort_values("timestamp")
        logs = np.log(
            group.close.to_numpy() / np.r_[group.open.iloc[0], group.close.to_numpy()[:-1]]
        )
        within = float(logs @ logs)
        overnight = None
        if (day, symbol) in lookup.index:
            row = lookup.loc[(day, symbol)]
            if pd.notna(row.previous_adjusted_close) and row.previous_adjusted_close > 0:
                overnight = float(
                    np.log(
                        group.open.iloc[0]
                        * (row.adjusted_close / row.raw_close)
                        / row.previous_adjusted_close
                    )
                )
        fingerprint = hashlib.sha256(
            pd.util.hash_pandas_object(group, index=False).to_numpy().tobytes()
        ).hexdigest()
        output.append(
            {
                "date": day,
                "symbol": symbol,
                "intraday_variance": within,
                "overnight_return": overnight,
                "realized_variance": None if overnight is None else within + overnight**2,
                "bars": 48,
                "source_hash": fingerprint,
                "method": "5m_squared_log_returns_plus_adjusted_overnight",
            }
        )
    return pd.DataFrame(output)


def render_intraday_recipe(*, start, end, symbols):
    return f'''import pandas as pd
from alphalab.data_sdk.v1 import data_recipe, rq
from alphalab.dataio import DataLoadError
from alphalab.dataio.option_intraday import normalize_intraday, intraday_variance
from alphalab.dataio.option_snapshots import validate_option_dates
from alphalab.dataio.symbols import to_rq_symbol


@data_recipe(id="etf_intraday", label="ETF five-minute realized variance", template="rq.etf_option_intraday")
def etf_intraday(context, *, start: str = {start!r}, end: str = {end!r}, symbols: tuple[str, ...] = {tuple(symbols)!r}):
    """Persist complete ETF five-minute sessions and derived realized variance."""
    validate_option_dates(start, end)
    symbols = tuple(dict.fromkeys(to_rq_symbol(value) for value in symbols))
    if context.mode == "plan":
        context.expect("rq.option_etf_5m", "rq.get_price(frequency=5m)", start=start, end=end, symbols=list(symbols))
        context.expect("research.option_intraday_variance", "complete 48-bar realized variance + adjusted overnight")
        return
    dates = [pd.Timestamp(v).normalize() for v in rq.get_trading_dates(start, end, market="cn")]
    try:
        existing = context.read("rq.option_etf_5m")
        counts = existing.groupby(["date", "symbol"]).size().to_dict()
    except DataLoadError:
        counts = {{}}
    pending = [day for day in dates if any(counts.get((day, symbol)) != 48 for symbol in symbols) or day in dates[-3:]]
    for offset in range(0, len(pending), 20):
        batch = pending[offset:offset+20]
        # Raw intraday returns are scale invariant. Overnight returns below use
        # the full daily adjustment history, including ex-distribution dates.
        raw = rq.get_price(list(symbols), start_date=str(batch[0].date()), end_date=str(batch[-1].date()), frequency="5m", fields=["open", "close"], adjust_type="none", expect_df=True)
        frame = normalize_intraday(raw)
        keys = set(zip(frame.date, frame.symbol))
        if any((day, symbol) not in keys for day in batch for symbol in symbols):
            raise ValueError("Missing requested five-minute ETF session")
        context.publish("rq.option_etf_5m", frame)
    history = context.read("rq.option_underlyings")
    # Recompute compact daily variance after any raw correction or daily rebase.
    for frame in context.iter_partitions("rq.option_etf_5m"):
        frame = frame.loc[frame.symbol.isin(symbols) & pd.to_datetime(frame.date).between(pd.Timestamp(start), pd.Timestamp(end))]
        if not frame.empty:
            context.publish("research.option_intraday_variance", intraday_variance(frame, history))
'''
