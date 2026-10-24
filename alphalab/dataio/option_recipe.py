"""Visible RQ option recipe source; uses the existing Data SDK execution boundary."""

import ast
import textwrap


def render_option_recipe(*, start, end, symbols=None):
    from alphalab.dataio.option_intraday import render_intraday_recipe
    from alphalab.dataio.recipes import render_builtin_recipe

    source = (
        _SOURCE.replace("__START__", repr(start))
        .replace("__END__", repr(end))
        .replace("__SYMBOLS__", repr(tuple(symbols)) if symbols else "('510050.XSHG',)")
    )
    selected = tuple(symbols) if symbols else ("510050.XSHG",)
    market = render_builtin_recipe("rq.etf_daily", start=start, end=end, symbols=selected)
    market = market.replace("CHUNK_DAYS = 366", "CHUNK_DAYS = 3660").replace(
        "FORCE = False", "FORCE = True"
    )
    intraday = render_intraday_recipe(start=start, end=end, symbols=selected)
    return (
        source
        + _nested_recipe(market, "_sync_etf_market", "research_data")
        + _nested_recipe(intraday, "_sync_etf_intraday", "etf_intraday")
    )


def _nested_recipe(source, name, entrypoint):
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == entrypoint:
            for decorator in reversed(node.decorator_list):
                del lines[decorator.lineno - 1 : decorator.end_lineno]
    return (
        f'\n\ndef {name}(context, *, start, end, symbols):\n    """Execute the complete visible acquisition source below."""\n'
        + textwrap.indent("\n".join(lines), "    ")
        + f"\n    {entrypoint}(context, start=start, end=end, symbols=symbols)\n"
    )


_SOURCE = '''import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import pandas as pd

from alphalab.data_sdk.v1 import data_recipe, rq
from alphalab.dataio import DataLoadError
from alphalab.dataio.option_rq import normalize, recover_daily_closes
from alphalab.dataio.option_snapshots import (
    normalize_option_snapshot, option_sync_dates, validate_option_dates,
)
from alphalab.dataio.symbols import to_rq_symbol
from alphalab.options.curves import normalize_yield_curve
from alphalab.dataio.option_underlyings import normalize_etf_history

OVERLAP_SESSIONS = 3
BATCH_SIZE = 100
CHUNK_SESSIONS = 10
DISCOVERY_WORKERS = 4
UNDERLYING_WORKERS = 3
DAILY_MASTER_DISCOVERY = True
INCLUDE_RATES = True
INCLUDE_UNDERLYINGS = True
INCLUDE_OPTIONS = True
INCLUDE_RESEARCH = True
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
    symbols = tuple(dict.fromkeys(to_rq_symbol(value) for value in symbols))
    if context.mode == "plan":
        if INCLUDE_OPTIONS:
            context.expect(
                "rq.option_snapshots",
                "rq.get_trading_dates + rq.all_instruments + dated listing intervals + rq.instruments + "
                "rq.get_price + rq.options.get_contract_property",
                start=start, end=end, symbols=list(symbols), overlap_sessions=OVERLAP_SESSIONS,
            )
        if INCLUDE_RATES:
            context.expect("rq.option_rates", "rq.get_yield_curve", start=start, end=end)
        if INCLUDE_UNDERLYINGS:
            context.expect("rq.option_underlyings", "rq.get_price", start=start, end=end)
        if INCLUDE_RESEARCH:
            _sync_etf_market(context, start=start, end=end, symbols=symbols)
            _sync_etf_intraday(context, start=start, end=end, symbols=symbols)
            context.expect("research.option_features", "local dated literature features", start=start, end=end)
        return

    # The plan never contacts RQ. Execution uses the exchange calendar, not weekdays.
    dates = _retry(lambda: rq.get_trading_dates(start, end, market="cn"))
    if any(not pd.Timestamp(start) <= pd.Timestamp(d) <= pd.Timestamp(end) for d in dates):
        raise ValueError("RQ calendar returned dates outside the requested interval")
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
    sessions_by_underlying = {symbol: dates for symbol in symbols}
    if DAILY_MASTER_DISCOVERY and INCLUDE_OPTIONS:
        # The undated RQ master retains expired contracts. Its first listing
        # establishes availability; never manufacture pre-listing observations.
        history_master = _retry(lambda: rq.all_instruments(type="Option"))
        required = {"order_book_id", "underlying_order_book_id", "listed_date", "de_listed_date"}
        if not isinstance(history_master, pd.DataFrame) or not required.issubset(history_master):
            raise ValueError("Invalid complete option listing history")
        for symbol in symbols:
            listings = pd.to_datetime(history_master.loc[
                history_master.underlying_order_book_id.eq(symbol), "listed_date"
            ], errors="raise")
            if listings.empty or listings.isna().any():
                raise ValueError("Missing ETF option listing history")
            sessions_by_underlying[symbol] = [day for day in dates if pd.Timestamp(day) >= listings.min()]
        history_master = history_master.loc[history_master.underlying_order_book_id.isin(symbols)].copy()
        history_master["listed_date"] = pd.to_datetime(history_master.listed_date, errors="raise")
        history_master["de_listed_date"] = pd.to_datetime(history_master.de_listed_date.replace("0000-00-00", None), errors="raise")
    pending_by_underlying = {}
    for requested in (symbols if INCLUDE_OPTIONS else ()):
        underlying = to_rq_symbol(requested)
        parts = list(context.iter_partitions("rq.option_snapshots", start=start, end=end, symbols={underlying}))
        existing = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        pending_by_underlying[underlying] = option_sync_dates(existing, sessions_by_underlying[underlying], underlying, overlap=OVERLAP_SESSIONS)
    masters = {}
    if DAILY_MASTER_DISCOVERY:
        # RQ's all_instruments(date=day) filters the same cached complete master
        # by listed_date <= day <= de_listed_date for Option (inclusive expiry).
        # Apply its exact interval rule on the selected ETF subset, avoiding
        # hundreds of repeated Python scans of the entire commodity market.
        pending_sets = {u: set(values) for u, values in pending_by_underlying.items()}
        def discover_master(day):
            timestamp = pd.Timestamp(day)
            frame = history_master.loc[history_master.listed_date.le(timestamp) & (
                history_master.de_listed_date.isna() | history_master.de_listed_date.ge(timestamp)
            )]
            result = {u: sorted(set(g.order_book_id.astype(str))) for u, g in frame.groupby("underlying_order_book_id")}
            for underlying, pending in pending_sets.items():
                if day in pending and not result.get(underlying):
                    raise ValueError(f"No historical contracts for {underlying} on {day}")
            return day, result
        pending_days = sorted(set().union(*pending_sets.values())) if pending_sets else []
        with ThreadPoolExecutor(max_workers=DISCOVERY_WORKERS) as workers:
            masters = dict(workers.map(discover_master, pending_days))
    publish_lock = Lock()
    def sync_underlying(requested):
        published = 0
        underlying = to_rq_symbol(requested)
        pending = pending_by_underlying[underlying]
        # Bulk request bounded session windows while retaining daily discovery.
        for chunk_offset in range(0, len(pending), CHUNK_SESSIONS):
            chunk = pending[chunk_offset:chunk_offset + CHUNK_SESSIONS]
            first, last = chunk[0], chunk[-1]
            # RQ's connection pool supports independent read calls. Bound the
            # discovery concurrency while preserving deterministic publication.
            def discover(day):
                return day, sorted(set(_retry(lambda: rq.options.get_contracts(underlying, trading_date=day))))
            if DAILY_MASTER_DISCOVERY:
                expected = {day: masters[day][underlying] for day in chunk}
            else:
                with ThreadPoolExecutor(max_workers=DISCOVERY_WORKERS) as workers:
                    expected = dict(workers.map(discover, chunk))
            for day in chunk:
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
                inconsistent = daily_bars.loc[daily_bars.volume.gt(0) & (
                    daily_bars.close.gt(daily_bars.high + 1e-10) |
                    daily_bars.close.lt(daily_bars.low - 1e-10)
                )]
                if not inconsistent.empty:
                    # Some RQ pre-adjustment-session closes use the next contract
                    # scale. Require complete independent minute evidence and
                    # the next dated strike/multiplier adjustment, retaining both.
                    minute = _retry(lambda: rq.get_price(
                        inconsistent.order_book_id.tolist(), start_date=day, end_date=day,
                        frequency="5m", fields=["open", "high", "low", "close", "volume"],
                        adjust_type="none", expect_df=True,
                    ))
                    next_properties = normalize(_retry(lambda: rq.options.get_contract_property(
                        inconsistent.order_book_id.tolist(), start_date=day,
                        end_date=str((timestamp + pd.Timedelta(days=30)).date()),
                        fields=["strike_price", "contract_multiplier"],
                    )))
                    daily_bars = recover_daily_closes(daily_bars, minute, next_properties, properties)
                # Unexpected source contracts must fail the coverage check, never be dropped.
                daily_properties = properties.loc[properties.date.eq(timestamp)]
                snapshot = normalize_option_snapshot(
                    daily_bars, daily_properties, instruments,
                    spot.loc[spot.date.eq(timestamp)], expected[day], underlying, day,
                )
                snapshot["expected_sessions"] = json.dumps([
                    pd.Timestamp(value).date().isoformat() for value in sessions_by_underlying[underlying]
                ])
                # Runtime publication is fail-fast/exclusive. Keep network reads
                # parallel, but serialize writes through this shared context.
                with publish_lock:
                    context.publish("rq.option_snapshots", snapshot)
                published += 1
                print(f"Published {underlying} {day}: {len(snapshot)} contracts", flush=True)
        return published
    with ThreadPoolExecutor(max_workers=UNDERLYING_WORKERS) as workers:
        published = sum(workers.map(sync_underlying, symbols if INCLUDE_OPTIONS else ()))
    context.output("published_option_sessions", published)
    if INCLUDE_OPTIONS and len(dates) > 0:
        context.require_coverage("rq.option_snapshots", start=start, end=end, fail_on_gap=True)
    if INCLUDE_RESEARCH:
        from alphalab.dataio.option_features import iter_feature_batches
        # These calls share the existing context and job; all acquisition source
        # is visible below, and no vendor access happens at module import time.
        _sync_etf_market(context, start=start, end=end, symbols=symbols)
        _sync_etf_intraday(context, start=start, end=end, symbols=symbols)
        for batch in iter_feature_batches(context, start=start, end=end, symbols=symbols):
            context.publish("research.option_features", batch, wait_seconds=60)
'''
