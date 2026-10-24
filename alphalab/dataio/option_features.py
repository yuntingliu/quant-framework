"""Source-fingerprinted incremental option predictors through the Data SDK."""

import hashlib
import json
from bisect import bisect_right
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from alphalab.options import DiscountCurve, analyze_chain
from alphalab.options.features import FORMULA_VERSION, option_features
from alphalab.options.literature import FEATURES

from .errors import DataLoadError
from .symbols import to_rq_symbol


def _digest(frame):
    return hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes()
    ).hexdigest()


def iter_feature_batches(context, *, start, end, symbols):
    """Rebuild changed source days; persist missing indicators as NaN, never ffill."""
    rates = context.read("rq.option_rates")
    history = context.read("rq.option_underlyings")
    try:
        intraday = context.read("research.option_intraday_variance")
    except DataLoadError:
        intraday = pd.DataFrame(columns=["date", "symbol", "realized_variance"])
    try:
        existing = context.read("research.option_features")
        prior = {
            (str(pd.Timestamp(r.date).date()), r.symbol): r.source_hash
            for r in existing.itertuples()
        }
    except DataLoadError:
        prior = {}
    histories = {symbol: frame.sort_values("date") for symbol, frame in history.groupby("symbol")}
    # Prefix hashes preserve the exact previous fingerprint while avoiding a
    # repeated pandas hash/date parse of all earlier history for every session.
    history_hashes = {
        s: pd.util.hash_pandas_object(f, index=False).to_numpy() for s, f in histories.items()
    }
    hf_histories = {s: f.sort_values("date") for s, f in intraday.groupby("symbol")}
    hf_hashes = {
        s: pd.util.hash_pandas_object(f, index=False).to_numpy() for s, f in hf_histories.items()
    }
    rate_groups = {pd.Timestamp(d): f for d, f in rates.groupby("date")}
    rate_dates = sorted(rate_groups)
    rate_hashes = {d: _digest(f) for d, f in rate_groups.items()}
    selected = {to_rq_symbol(value) for value in symbols}
    batch = []
    for frame in context.iter_partitions(
        "rq.option_snapshots", start=start, end=end, symbols=selected
    ):
        if frame.empty:
            continue
        symbol, day = str(frame.underlying.iloc[0]), pd.Timestamp(frame.date.iloc[0])
        if symbol not in selected or not pd.Timestamp(start) <= day <= pd.Timestamp(end):
            continue
        dated = histories.get(symbol, history.iloc[:0])
        history_end = dated.date.searchsorted(day, side="right")
        dated = dated.iloc[:history_end]
        history_digest = hashlib.sha256(
            history_hashes.get(symbol, np.array([], dtype="uint64"))[:history_end].tobytes()
        ).hexdigest()
        hf = hf_histories.get(symbol, intraday.iloc[:0])
        hf_end = hf.date.searchsorted(day, side="right")
        hf_start = max(0, hf_end - 20)
        hf = hf.iloc[hf_start:hf_end]
        hf_digest = hashlib.sha256(
            hf_hashes.get(symbol, np.array([], dtype="uint64"))[hf_start:hf_end].tobytes()
        ).hexdigest()
        rate_index = bisect_right(rate_dates, day) - 1
        rate_day = rate_dates[rate_index] if rate_index >= 0 else None
        available_rates = rate_groups[rate_day] if rate_day is not None else rates.iloc[:0]
        fingerprint = hashlib.sha256(
            (
                FORMULA_VERSION
                + _digest(frame)
                + history_digest
                + (rate_hashes[rate_day] if rate_day is not None else _digest(available_rates))
                + hf_digest
            ).encode()
        ).hexdigest()
        if prior.get((str(day.date()), symbol)) == fingerprint:
            continue
        reason = ""
        try:
            curve = DiscountCurve.as_of(available_rates, day)
            expiry_rates = {
                str(pd.Timestamp(v).date()): curve.rate(max(0, (pd.Timestamp(v) - day).days / 365))
                for v in frame.maturity_date.unique()
            }
            chain = analyze_chain(
                frame, rates_by_expiry=expiry_rates, carry_mode="put_call_forward"
            )
            row = option_features(chain, dated, hf)
            row["diagnostics"] = json.dumps(row["diagnostics"], allow_nan=False)
        except ValueError as exc:
            # Store the invalid observation, replacing prior values after corrections.
            reason = str(exc)[:180]
            row = {
                "date": day,
                "symbol": symbol,
                **{key: None for key in FEATURES},
                "diagnostics": "[]",
                "eligible": 0,
                "formula_version": FORMULA_VERSION,
                "availability": "session_close",
            }
        row.update(
            source_hash=fingerprint,
            source_retrieved_at=str(frame.retrieved_at.max()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            issue=reason,
        )
        batch.append(row)
        if len(batch) >= 30:
            yield pd.DataFrame(batch)
            batch = []
    if batch:
        yield pd.DataFrame(batch)


def validate_feature_frame(frame):
    required = {"date", "symbol", "source_hash", "formula_version", "availability", *FEATURES}
    if frame.empty or not required.issubset(frame):
        raise ValueError("Incomplete option feature schema")
    if (
        frame.duplicated(["date", "symbol"]).any()
        or pd.to_datetime(frame.date, errors="coerce").isna().any()
    ):
        raise ValueError("Invalid or duplicate option feature dates")
    if (
        not frame.availability.eq("session_close").all()
        or not frame.formula_version.eq(FORMULA_VERSION).all()
    ):
        raise ValueError("Invalid option predictor availability or version")
    values = frame[list(FEATURES)].apply(pd.to_numeric, errors="raise")
    if np.isinf(values.to_numpy(dtype=float)).any():
        raise ValueError("Infinite option feature values")


def render_feature_recipe(*, start, end, symbols):
    return f'''from alphalab.data_sdk.v1 import data_recipe
from alphalab.dataio.option_features import iter_feature_batches


@data_recipe(id="option_features", label="Option research features", template="research.option_features")
def option_features(context, *, start: str = {start!r}, end: str = {end!r}, symbols: tuple[str, ...] = {tuple(symbols)!r}):
    """Materialize changed, dated research predictors without contacting a vendor."""
    if context.mode == "plan":
        context.expect("research.option_features", "local dated option snapshots + curves + adjusted history", start=start, end=end, symbols=list(symbols))
        return
    # Reads are bounded per source partition; hashes retain unchanged feature days.
    published = 0
    for batch in iter_feature_batches(context, start=start, end=end, symbols=symbols):
        context.publish("research.option_features", batch, wait_seconds=60)
        published += len(batch)
    context.output("published_feature_rows", published)
'''
