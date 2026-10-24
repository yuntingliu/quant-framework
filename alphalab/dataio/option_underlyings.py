"""Adjusted ETF history for realized volatility; raw prices remain pricing inputs."""

import numpy as np
import pandas as pd

from .option_rq import normalize


def normalize_etf_history(raw, adjusted):
    raw, adjusted = normalize(raw), normalize(adjusted)
    keys = ["date", "order_book_id"]
    if not set(keys + ["close"]).issubset(raw) or not set(keys + ["close"]).issubset(adjusted):
        raise ValueError("ETF history requires dated raw and adjusted closes")
    out = (
        raw[keys + ["close"]]
        .rename(columns={"close": "raw_close"})
        .merge(
            adjusted[keys + ["close"]].rename(columns={"close": "adjusted_close"}),
            on=keys,
            how="outer",
            validate="one_to_one",
        )
    )
    prices = out[["raw_close", "adjusted_close"]].apply(pd.to_numeric, errors="coerce")
    if (
        out.empty
        or out[keys].isna().any().any()
        or not np.isfinite(prices.to_numpy()).all()
        or prices.le(0).any().any()
    ):
        raise ValueError("Missing or invalid aligned ETF history")
    out["symbol"] = out.pop("order_book_id")
    out["source"] = "rqdata"
    out["adjustment"] = "pre_adjusted_for_realized_returns; raw_close_for_reference"
    return out.sort_values(["date", "symbol"]).reset_index(drop=True)
