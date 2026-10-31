"""Recompute the point-in-time leak from the fixture with plain pandas.

This is deliberately independent of alphalab: it reads the committed parquet
partitions directly and joins availability against first disclosure, so the
expected answer never comes from the quality checks being graded. It must agree
with the plant recorded in benchmark/data/pit-leak-small/fixture.json.

    python -m benchmark.reference.dev.data-pit-leak-crosscheck.compute  # not importable
    python benchmark/reference/dev/data-pit-leak-crosscheck/compute.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

FIXTURE = Path(__file__).resolve().parents[3] / "data" / "pit-leak-small"


def _read(relative: str) -> pd.DataFrame:
    files = sorted((FIXTURE / relative).rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No partitions under {FIXTURE / relative}")
    return pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)


def leaked_rows() -> pd.DataFrame:
    fundamentals = _read("canonical/fundamentals")[["symbol", "quarter", "available_date"]]
    disclosures = _read("rq/financials/statement=income")
    first = (
        disclosures.loc[disclosures["if_adjusted"].eq(0)]
        .sort_values(["symbol", "quarter", "info_date"])
        .drop_duplicates(["symbol", "quarter"], keep="first")[["symbol", "quarter", "info_date"]]
    )
    merged = fundamentals.merge(first, on=["symbol", "quarter"], how="left", validate="one_to_one")
    merged["available_date"] = pd.to_datetime(merged["available_date"])
    merged["info_date"] = pd.to_datetime(merged["info_date"])
    leaked = merged.loc[merged["available_date"].lt(merged["info_date"])].copy()
    leaked["days_early"] = (leaked["info_date"] - leaked["available_date"]).dt.days
    return leaked.sort_values(["symbol", "quarter"], ignore_index=True)


def main() -> None:
    leaked = leaked_rows()
    payload = {
        "leaked_rows": [
            {
                "symbol": row.symbol,
                "quarter": row.quarter,
                "available_date": row.available_date.strftime("%Y-%m-%d"),
                "disclosed_on": row.info_date.strftime("%Y-%m-%d"),
                "days_early": int(row.days_early),
            }
            for row in leaked.itertuples(index=False)
        ],
        "keys": [f"{row.symbol}:{row.quarter}" for row in leaked.itertuples(index=False)],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
