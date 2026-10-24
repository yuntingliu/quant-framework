"""Visible RQ option recipe source; uses the existing Data SDK execution boundary."""


def render_option_recipe(*, start, end, symbols=None):
    return (
        _SOURCE.replace("__START__", repr(start))
        .replace("__END__", repr(end))
        .replace("__SYMBOLS__", repr(tuple(symbols)) if symbols else "('510050.XSHG',)")
    )


_SOURCE = '''import json
import time
import pandas as pd

from alphalab.data_sdk.v1 import data_recipe, rq
from alphalab.dataio import DataLoadError
from alphalab.dataio.option_rq import normalize
from alphalab.dataio.option_snapshots import (
    normalize_option_snapshot, option_sync_dates, validate_option_dates,
)
from alphalab.dataio.symbols import to_rq_symbol
from alphalab.options.curves import normalize_yield_curve
from alphalab.dataio.option_underlyings import normalize_etf_history

OVERLAP_SESSIONS = 3
BATCH_SIZE = 100
CHUNK_SESSIONS = 10
INCLUDE_RATES = True
INCLUDE_UNDERLYINGS = True
INCLUDE_OPTIONS = True
RETRIES = 3


def _retry(call):
    """Retry transient vendor calls without exposing connection credentials."""
    for attempt in range(RETRIES):
        try:
            return call()
        except Exception:
            if attempt + 1 == RETRIES:
                raise ValueError("RQ option request failed; check connection and permissions") from None
            time.sleep(0.5 * 2**attempt)


@data_recipe(id="etf_options", label="ETF options daily", template="rq.etf_options")
def etf_options(
    context,
    *,
    start: str = __START__,
    end: str = __END__,
    symbols: tuple[str, ...] | None = __SYMBOLS__,
):
    """Publish complete daily chains with dated properties and a discovery manifest."""
    validate_option_dates(start, end)
    if not symbols:
        raise ValueError("Select explicit ETF underlyings")
    if context.mode == "plan":
        if INCLUDE_OPTIONS:
            context.expect(
                "rq.option_snapshots",
                "rq.get_trading_dates + rq.options.get_contracts + rq.instruments + "
                "rq.get_price + rq.options.get_contract_property",
                start=start, end=end, symbols=list(symbols), overlap_sessions=OVERLAP_SESSIONS,
            )
        if INCLUDE_RATES:
            context.expect("rq.option_rates", "rq.get_yield_curve", start=start, end=end)
        if INCLUDE_UNDERLYINGS:
            context.expect("rq.option_underlyings", "rq.get_price", start=start, end=end)
        return

    # The plan never contacts RQ. Execution uses the exchange calendar, not weekdays.
    dates = _retry(lambda: rq.get_trading_dates(start, end, market="cn"))
    if any(not pd.Timestamp(start) <= pd.Timestamp(d) <= pd.Timestamp(end) for d in dates):
        raise ValueError("RQ calendar returned dates outside the requested interval")
    try:
        existing = context.read("rq.option_snapshots")
    except DataLoadError:
        existing = pd.DataFrame()
    if INCLUDE_RATES and len(dates):
        try:
            stored_rates = context.read("rq.option_rates")
            rate_dates = set(pd.to_datetime(stored_rates.date).dt.normalize())
        except DataLoadError:
            rate_dates = set()
        if OVERLAP_SESSIONS or any(pd.Timestamp(d).normalize() not in rate_dates for d in dates):
            raw_rates = _retry(lambda: rq.get_yield_curve(start_date=start, end_date=end, market="cn"))
            context.publish("rq.option_rates", normalize_yield_curve(raw_rates))
    if INCLUDE_UNDERLYINGS and len(dates):
        try:
            stored_etfs = context.read("rq.option_underlyings")
            stored_keys = set(zip(pd.to_datetime(stored_etfs.date).dt.normalize(), stored_etfs.symbol))
        except DataLoadError:
            stored_etfs = pd.DataFrame()
            stored_keys = set()
        rq_symbols = [to_rq_symbol(value) for value in symbols]
        missing_history = any((pd.Timestamp(day).normalize(), symbol) not in stored_keys
                              for day in dates for symbol in rq_symbols)
        if missing_history or OVERLAP_SESSIONS:
            # Rebase all retained history together: pre-adjusted levels can change
            # after a distribution, so merging only new dates invents a return jump.
            retained = (stored_etfs.loc[stored_etfs.symbol.isin(rq_symbols)]
                        if not stored_etfs.empty else stored_etfs)
            history_start = min(str(start), str(pd.Timestamp(retained.date.min()).date())) if not retained.empty else start
            history_end = max(str(end), str(pd.Timestamp(retained.date.max()).date())) if not retained.empty else end
            raw_etfs = _retry(lambda: rq.get_price(
                rq_symbols, start_date=history_start, end_date=history_end, fields=["close"],
                frequency="1d", adjust_type="none", expect_df=True,
            ))
            adjusted_etfs = _retry(lambda: rq.get_price(
                rq_symbols, start_date=history_start, end_date=history_end, fields=["close"],
                frequency="1d", adjust_type="pre", expect_df=True,
            ))
            history = normalize_etf_history(raw_etfs, adjusted_etfs)
            required = {(d, s) for d, s in stored_keys if s in rq_symbols}
            required.update((pd.Timestamp(d).normalize(), s) for d in dates for s in rq_symbols)
            actual = set(zip(pd.to_datetime(history.date).dt.normalize(), history.symbol))
            if not required.issubset(actual):
                raise ValueError("ETF history is missing requested or retained sessions")
            context.publish("rq.option_underlyings", history)
    published = 0
    for requested in (symbols if INCLUDE_OPTIONS else ()):
        underlying = to_rq_symbol(requested)
        pending = option_sync_dates(existing, dates, underlying, overlap=OVERLAP_SESSIONS)
        # Bulk request bounded session windows while retaining daily discovery.
        for chunk_offset in range(0, len(pending), CHUNK_SESSIONS):
            chunk = pending[chunk_offset:chunk_offset + CHUNK_SESSIONS]
            first, last = chunk[0], chunk[-1]
            expected = {}
            for day in chunk:
                expected[day] = sorted(set(_retry(lambda: rq.options.get_contracts(underlying, trading_date=day))))
                if not expected[day]:
                    raise ValueError("No ETF option contracts discovered for the requested session")
            ids = sorted(set().union(*map(set, expected.values())))
            spot = normalize(_retry(lambda: rq.get_price(
                underlying, start_date=first, end_date=last, frequency="1d",
                fields=["close", "volume"], adjust_type="none", expect_df=True,
            )))
            bar_parts, property_parts, instruments = [], [], []
            for offset in range(0, len(ids), BATCH_SIZE):
                batch = ids[offset:offset + BATCH_SIZE]
                info = _retry(lambda: rq.instruments(batch))
                instruments.extend(info if isinstance(info, (list, tuple)) else [info])
                bar_parts.append(normalize(_retry(lambda: rq.get_price(
                    batch, start_date=first, end_date=last, frequency="1d",
                    fields=["open", "high", "low", "close", "settlement", "volume", "open_interest"],
                    adjust_type="none", expect_df=True,
                ))))
                # Latest instrument strike/multiplier must never overwrite pre-dividend history.
                property_parts.append(normalize(_retry(lambda: rq.options.get_contract_property(
                    batch, start_date=first, end_date=last,
                    fields=["strike_price", "contract_multiplier"],
                ))))
            bars = pd.concat(bar_parts, ignore_index=True)
            properties = pd.concat(property_parts, ignore_index=True)
            if bars.empty or properties.empty or spot.empty:
                raise ValueError("Empty option history chunk")
            for day in chunk:
                timestamp = pd.Timestamp(day)
                daily_bars = bars.loc[bars.date.eq(timestamp)].copy()
                # Unexpected source contracts must fail the coverage check, never be dropped.
                daily_properties = properties.loc[properties.date.eq(timestamp)]
                snapshot = normalize_option_snapshot(
                    daily_bars, daily_properties, instruments,
                    spot.loc[spot.date.eq(timestamp)], expected[day], underlying, day,
                )
                snapshot["expected_sessions"] = json.dumps([
                    pd.Timestamp(value).date().isoformat() for value in dates
                ])
                context.publish("rq.option_snapshots", snapshot)
                published += 1
                print(f"Published {underlying} {day}: {len(snapshot)} contracts", flush=True)
    context.output("published_option_sessions", published)
    if INCLUDE_OPTIONS and len(dates) > 0:
        context.require_coverage("rq.option_snapshots", start=start, end=end, fail_on_gap=True)
'''
