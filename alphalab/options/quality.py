"""Strict daily snapshot schema and coverage checks; never fill missing quotes."""

import numpy as np
import pandas as pd

from .quote_evidence import validate_close_evidence


class QualityError(ValueError):
    pass


NUMERIC = [
    "open",
    "high",
    "low",
    "close",
    "settlement",
    "volume",
    "open_interest",
    "strike_price",
    "contract_multiplier",
    "spot",
]
REQUIRED = [
    "date",
    "order_book_id",
    "underlying",
    "option_type",
    "exercise_type",
    "listed_date",
    "maturity_date",
    *NUMERIC,
]


def validate_snapshot(frame: pd.DataFrame, expected_ids, day, underlying: str) -> dict:
    missing = sorted(set(REQUIRED) - set(frame.columns))
    if missing:
        raise QualityError(f"Missing columns: {missing}")
    if frame.empty:
        raise QualityError("Empty snapshot; cannot certify coverage")
    if frame[REQUIRED].isna().any().any():
        raise QualityError("Null values in required fields")
    if frame.duplicated(["date", "order_book_id"]).any():
        raise QualityError("Duplicate date/contract keys")
    expected = {str(x) for x in expected_ids}
    actual = set(frame.order_book_id.astype(str))
    if actual != expected:
        raise QualityError(
            f"Contract coverage mismatch: missing={sorted(expected - actual)[:10]}, "
            f"unexpected={sorted(actual - expected)[:10]}"
        )
    if not frame.underlying.eq(underlying).all():
        raise QualityError("Underlying mismatch")
    date = pd.Timestamp(day).normalize()
    dates = pd.to_datetime(frame.date, errors="coerce")
    listed = pd.to_datetime(frame.listed_date, errors="coerce")
    expiry = pd.to_datetime(frame.maturity_date, errors="coerce")
    if dates.isna().any() or listed.isna().any() or expiry.isna().any():
        raise QualityError("Invalid dates")
    if not dates.eq(date).all() or (dates < listed).any() or (dates > expiry).any():
        raise QualityError("Quote date outside requested date/contract lifetime")
    if not frame.option_type.isin(["C", "P"]).all() or not frame.exercise_type.eq("E").all():
        raise QualityError("Only European C/P ETF options are supported")
    numeric = frame[NUMERIC].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise QualityError("Nonfinite or nonnumeric values")
    if numeric.lt(0).any().any():
        raise QualityError("Negative prices, activity or contract properties")
    if numeric[["strike_price", "contract_multiplier", "spot"]].le(0).any().any():
        raise QualityError("Nonpositive strike, multiplier or spot")
    if numeric.spot.nunique() != 1:
        raise QualityError("Inconsistent same-day ETF spot")
    # A zero-volume RQ option can carry a reference close that differs from its
    # non-trading OHLC placeholders. It is not a transaction price. Validate OHL
    # for every row, and require the close inside the range only when traded.
    tolerance = 1e-10
    if (numeric.high + tolerance < numeric[["open", "low"]].max(axis=1)).any():
        raise QualityError("OHLC high is inconsistent")
    if (numeric.low - tolerance > numeric[["open", "high"]].min(axis=1)).any():
        raise QualityError("OHLC low is inconsistent")
    close_outside = (numeric.close > numeric.high + tolerance) | (
        numeric.close < numeric.low - tolerance
    )
    if (close_outside & numeric.volume.gt(0)).any():
        raise QualityError("Traded close is outside the OHLC range")
    try:
        recovered = validate_close_evidence(frame)
    except (ValueError, TypeError, KeyError) as exc:
        raise QualityError(str(exc)) from None
    return {
        "status": "passed",
        "expected": len(expected),
        "rows": len(frame),
        "coverage": 1.0,
        "zero_volume": int(numeric.volume.eq(0).sum()),
        "zero_close": int(numeric.close.eq(0).sum()),
        "zero_volume_close_outside_range": int((close_outside & numeric.volume.eq(0)).sum()),
        "adjusted_contracts": int(numeric.contract_multiplier.ne(10000).sum()),
        "reconstructed_closes": recovered,
    }
