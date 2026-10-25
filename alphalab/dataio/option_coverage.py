"""Exact ETF/session coverage for compact option datasets."""

import pandas as pd

from .symbols import to_rq_symbol


def compact_option_gaps(store, dataset, frame, start=None, end=None, symbols=None):
    if "symbol" not in frame or frame.empty:
        return {}
    dates = pd.to_datetime(frame.date)
    lower, upper = (
        pd.Timestamp(start) if start else dates.min(),
        pd.Timestamp(end) if end else dates.max(),
    )
    requested = {to_rq_symbol(s) for s in symbols} if symbols else None
    actual = {(pd.Timestamp(d), to_rq_symbol(s)) for d, s in zip(dates, frame.symbol)}
    if dataset == "research.option_features":
        expected = set()
        for path in store.catalog.files("rq.option_snapshots"):
            day = pd.Timestamp(path.parent.parent.name.removeprefix("date="))
            if lower <= day <= upper and (
                requested is None or path.parent.name.removeprefix("underlying=") in requested
            ):
                expected.add((day, path.parent.name.removeprefix("underlying=")))
    else:
        expected = set()
        reference = (
            "rq.option_underlyings"
            if dataset in {"rq.option_etf_5m", "research.option_intraday_variance"}
            else "rq.bars"
        )
        if store.catalog.files(reference):
            ref = store.read(reference)
            selected = {to_rq_symbol(s) for s in frame.symbol}
            expected = {
                (pd.Timestamp(d), to_rq_symbol(s))
                for d, s in zip(ref.date, ref.symbol)
                if to_rq_symbol(s) in selected and lower <= pd.Timestamp(d) <= upper
            }
    # Standalone imports have no snapshot/reference grid. Detect internal
    # per-asset holes using the union calendar, bounded by observed lifetimes.
    if not expected:
        sessions = sorted({d for d, _ in actual if lower <= d <= upper})
        for symbol in frame.symbol.unique():
            own = sorted(d for d, s in actual if s == to_rq_symbol(symbol))
            expected.update((d, to_rq_symbol(symbol)) for d in sessions if own[0] <= d <= own[-1])
    missing = sorted(expected - actual)
    return {
        "required_keys": len(expected),
        "missing_keys": len(missing),
        "missing_key_sample": [[str(d.date()), s] for d, s in missing[:20]],
    }
