"""Canonical daily ETF option snapshots and gap-aware acquisition planning."""

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from alphalab.options.quality import QualityError, validate_snapshot

OPTION_DATASET = "rq.option_snapshots"


def normalize_option_snapshot(bars, properties, instruments, spot, expected_ids, underlying, day):
    """Join only identical daily keys; use dated strike/multiplier, never latest values."""
    from alphalab.dataio.option_rq import normalize

    bars, properties, spot = map(normalize, (bars, properties, spot))
    if (
        len(spot) != 1
        or "date" not in spot
        or "order_book_id" not in spot
        or not spot.date.eq(pd.Timestamp(day)).all()
        or not spot.order_book_id.eq(underlying).all()
    ):
        raise QualityError("Missing or misaligned same-day ETF spot")
    if not pd.notna(spot.iloc[0].volume) or float(spot.iloc[0].volume) <= 0:
        raise QualityError("ETF has no trading activity; spot may be stale")
    if not isinstance(instruments, (tuple, list)):
        instruments = [instruments]
    metadata = pd.DataFrame(
        [
            {
                "order_book_id": str(item.order_book_id),
                "underlying": item.underlying_order_book_id,
                "option_type": item.option_type,
                "exercise_type": item.exercise_type,
                "listed_date": item.listed_date,
                "maturity_date": item.maturity_date,
            }
            for item in instruments
        ]
    )
    if bars.empty or properties.empty:
        raise QualityError("Empty option bars or daily properties")
    frame = bars.merge(
        properties[["date", "order_book_id", "strike_price", "contract_multiplier"]],
        on=["date", "order_book_id"],
        how="left",
        validate="one_to_one",
    )
    frame = frame.merge(metadata, on="order_book_id", how="left", validate="many_to_one")
    frame["spot"] = float(spot.iloc[0].close)
    frame["symbol"] = frame.order_book_id
    frame["source"] = "rqdata"
    frame["frequency"] = "1d"
    frame["volume_unit"] = "contracts"
    frame["price_unit"] = "CNY_per_underlying_unit"
    frame["quote_status"] = frame.volume.gt(0).map({True: "traded", False: "no_trade_reference"})
    frame["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    frame["expected_contracts"] = json.dumps(sorted(set(str(x) for x in expected_ids)))
    frame["expected_sessions"] = json.dumps([pd.Timestamp(day).date().isoformat()])
    validate_snapshot(frame, expected_ids, day, underlying)
    return frame


def validate_option_partitions(frame):
    """Validate a complete persisted snapshot, including its discovery manifest."""
    required = {
        "date",
        "underlying",
        "expected_contracts",
        "expected_sessions",
        "symbol",
        "order_book_id",
    }
    if frame.empty or not required.issubset(frame):
        raise QualityError(
            "Option snapshot requires date, underlying, symbol and contract manifest"
        )
    if frame[list(required)].isna().any().any():
        raise QualityError("Null option snapshot keys or manifest")
    reports = []
    for (day, underlying), group in frame.groupby(["date", "underlying"]):
        if not re.fullmatch(r"[0-9]{6}\.(XSHG|XSHE)", str(underlying)):
            raise QualityError("ETF underlying must use a six-digit RQ exchange symbol")
        if not group.symbol.astype(str).eq(group.order_book_id.astype(str)).all():
            raise QualityError("Option symbol and order_book_id must agree")
        if group.expected_contracts.nunique() != 1:
            raise QualityError("Inconsistent contract discovery manifest")
        if group.expected_sessions.nunique() != 1:
            raise QualityError("Inconsistent expected trading session manifest")
        try:
            sessions = json.loads(group.expected_sessions.iloc[0])
            if (
                not isinstance(sessions, list)
                or not sessions
                or not all(isinstance(x, str) for x in sessions)
            ):
                raise ValueError("Invalid trading sessions")
            session_dates = pd.to_datetime(sessions, errors="raise")
            if session_dates.isna().any() or pd.Timestamp(day).normalize() not in session_dates:
                raise ValueError("Quote date missing from session manifest")
        except (ValueError, TypeError) as exc:
            raise QualityError("Invalid expected trading session manifest") from exc
        try:
            ids = json.loads(group.expected_contracts.iloc[0])
        except (ValueError, TypeError) as exc:
            raise QualityError("Invalid contract discovery manifest") from exc
        if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
            raise QualityError("Contract manifest must be a nonempty list of IDs")
        reports.append(
            {
                "day": pd.Timestamp(day).date().isoformat(),
                "underlying": underlying,
                **validate_snapshot(group, ids, day, underlying),
            }
        )
    return reports


def option_sync_dates(existing, trading_dates, underlying, *, overlap=3):
    """Find all missing/invalid sessions, plus a recent correction window."""
    if not isinstance(overlap, int) or overlap < 0:
        raise ValueError("overlap must be a nonnegative integer")
    days = sorted({pd.Timestamp(d).date().isoformat() for d in trading_dates})
    done = set()
    if not existing.empty and {"date", "underlying"}.issubset(existing):
        selected = existing.loc[existing.underlying.eq(underlying)]
        for day, group in selected.groupby("date"):
            try:
                validate_option_partitions(group)
                done.add(pd.Timestamp(day).date().isoformat())
            except (ValueError, TypeError, KeyError):
                continue
    recent = set(days[-overlap:]) if overlap else set()
    return [day for day in days if day not in done or day in recent]


def validate_option_dates(start, end):
    first, last = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    if pd.isna(first) or pd.isna(last) or first > last:
        raise ValueError("A valid ordered start/end interval is required")
    today = pd.Timestamp(datetime.now(ZoneInfo("Asia/Shanghai")).date())
    if last >= today:
        raise ValueError("Option daily sync end must be before today in Asia/Shanghai")
