"""Export a fixed Ricequant ETF option slice with calibration and tail labels."""

import argparse
import json
from math import exp, log
from pathlib import Path

import pandas as pd
from pyarrow.parquet import ParquetFile

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.option_data import load_option_snapshot
from alphalab.options import DiscountCurve


AS_OF = "2026-10-23"
EXPIRY = "2026-12-23"
TAIL_CUTOFF = 0.05


def load_panel(underlying: str, root: Path | None = None) -> dict:
    frame = load_option_snapshot(underlying, AS_OF, root=root)
    spot = float(frame.spot.iloc[0])
    underlying = frame.underlying.iloc[0]
    t = (pd.Timestamp(EXPIRY) - pd.Timestamp(AS_OF)).days / 365

    rate_path = DataCatalog(root).path("rq.option_rates") / "year=2026/month=10/part.parquet"
    rate = DiscountCurve.as_of(ParquetFile(rate_path).read().to_pandas(), AS_OF).rate(t)
    chain = frame.loc[
        pd.to_datetime(frame.maturity_date).eq(pd.Timestamp(EXPIRY))
        & frame.exercise_type.eq("E") & frame.contract_multiplier.eq(10_000)
        & frame.volume.gt(0) & frame.close.gt(0)
    ].sort_values("order_book_id")
    # Choose the most active listed contract if a standard side/strike is duplicated.
    chain = (
        chain.sort_values("volume", ascending=False, kind="stable")
        .drop_duplicates(["strike_price", "option_type"])
    )
    pairs = chain.pivot(index="strike_price", columns="option_type", values="close")
    pairs = pairs.reindex(columns=["C", "P"]).dropna()
    if len(pairs) < 3:
        raise ValueError(f"{underlying} needs at least three eligible put–call pairs for the carry estimate")
    strikes = sorted(pairs.index, key=lambda strike: (abs(log(strike / spot)), strike))[:3]
    anchors = pairs.loc[strikes]
    forward = float((anchors.index + exp(rate * t) * (anchors.C - anchors.P)).median())
    if forward <= 0:
        raise ValueError(f"{underlying} has a nonpositive implied forward")
    dividend_yield = rate - log(forward / spot) / t

    rows = []
    for row in chain.sort_values(["option_type", "strike_price"]).itertuples():
        k = float(row.strike_price)
        m = log(k / forward)
        if not ((row.option_type == "P" and m < 0) or (row.option_type == "C" and m > 0)):
            continue
        # Carry anchors may enter calibration but must never enter the scored holdout.
        sample = "calibration" if abs(m) <= TAIL_CUTOFF or k in strikes else "holdout"
        rows.append({
            "contract": str(row.order_book_id), "kind": row.option_type,
            "strike": k, "log_moneyness": m, "close_cny": float(row.close), "sample": sample,
        })
    counts = {sample: {kind: sum(r["sample"] == sample and r["kind"] == kind for r in rows)
                       for kind in ("P", "C")} for sample in ("calibration", "holdout")}
    if any(not n for sides in counts.values() for n in sides.values()):
        raise ValueError(f"{underlying} needs puts and calls in both subsets: {counts}")
    return {
        "underlying": underlying, "as_of": AS_OF, "expiry": EXPIRY, "maturity": t,
        "spot": spot, "forward": forward, "rate": rate, "dividend_yield": dividend_yield,
        "carry_anchor_strikes": [float(k) for k in strikes],
        "tail_cutoff": TAIL_CUTOFF, "counts": counts, "quotes": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--underlying", action="append",
                        help="Repeat for multiple Ricequant symbols; default CSI 300 and ChiNext")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    symbols = args.underlying or ["510300.XSHG", "159915.XSHE"]
    panels = [load_panel(symbol, args.data_root) for symbol in symbols]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(panels, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for panel in panels:
        print(f"{panel['underlying']}: {panel['counts']}")


if __name__ == "__main__":
    main()
