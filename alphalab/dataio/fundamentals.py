"""Point-in-time financial statement transforms for generic strategies."""
from __future__ import annotations

import numpy as np
import pandas as pd

CANONICAL_FIELDS = (
    "ep",
    "bp",
    "roe",
    "gross_margin",
    "leverage",
    "profit_growth",
    "revenue_growth",
)
INCOME_FIELDS = (
    "revenue",
    "operating_revenue",
    "cost_of_goods_sold",
    "gross_profit",
    "net_profit_parent_company",
)
BALANCE_FIELDS = (
    "total_assets",
    "total_liabilities",
    "equity_parent_company",
    "paid_in_capital",
)


def first_disclosures(frame: pd.DataFrame) -> pd.DataFrame:
    """Select the first originally reported row for every symbol and quarter."""

    if frame.empty:
        return frame.copy()
    result = frame.copy()
    result["info_date"] = pd.to_datetime(result["info_date"], errors="coerce")
    if "if_adjusted" in result:
        original = result["if_adjusted"].fillna(0).eq(0)
        if original.any():
            result = result.loc[original]
    return (
        result.dropna(subset=["symbol", "quarter", "info_date"])
        .sort_values(["symbol", "quarter", "info_date"])
        .drop_duplicates(["symbol", "quarter"], keep="first")
        .reset_index(drop=True)
    )


def build_canonical_fundamentals(
    income: pd.DataFrame,
    balance: pd.DataFrame,
    bars: pd.DataFrame,
    *,
    asof_date: str | None = None,
) -> pd.DataFrame:
    """Build strategy-ready PIT fields from first-disclosure RQ statements."""

    income_first = first_disclosures(income)
    balance_first = first_disclosures(balance)
    if income_first.empty or balance_first.empty or bars.empty:
        return _empty()

    income_first = income_first.rename(columns={"info_date": "income_date"})
    balance_first = balance_first.rename(columns={"info_date": "balance_date"})
    income_columns = ["symbol", "quarter", "income_date", *INCOME_FIELDS]
    balance_columns = ["symbol", "quarter", "balance_date", *BALANCE_FIELDS]
    income_columns = [name for name in income_columns if name in income_first]
    balance_columns = [name for name in balance_columns if name in balance_first]
    merged = income_first[income_columns].merge(
        balance_first[balance_columns],
        on=["symbol", "quarter"],
        how="inner",
    )
    if merged.empty:
        return _empty()
    merged["available_date"] = pd.concat(
        [
            pd.to_datetime(merged["income_date"], errors="coerce"),
            pd.to_datetime(merged["balance_date"], errors="coerce"),
        ],
        axis=1,
    ).max(axis=1)
    if asof_date is not None:
        merged = merged.loc[merged["available_date"].le(pd.Timestamp(asof_date))]
    merged = _quarter_parts(merged).sort_values(["symbol", "year", "quarter_no"])
    for column in (*INCOME_FIELDS, *BALANCE_FIELDS):
        if column in merged:
            merged[column] = pd.to_numeric(merged[column], errors="coerce")

    merged["revenue_base"] = merged.get("revenue", pd.Series(index=merged.index, dtype=float))
    if "operating_revenue" in merged:
        merged["revenue_base"] = merged["revenue_base"].combine_first(
            merged["operating_revenue"]
        )
    if "gross_profit" in merged:
        merged["gross_profit_base"] = merged["gross_profit"]
    else:
        merged["gross_profit_base"] = np.nan
    if "cost_of_goods_sold" in merged:
        merged["gross_profit_base"] = merged["gross_profit_base"].combine_first(
            merged["revenue_base"] - merged["cost_of_goods_sold"]
        )

    for source, target in (
        ("revenue_base", "revenue_single"),
        ("gross_profit_base", "gross_profit_single"),
        ("net_profit_parent_company", "profit_single"),
    ):
        if source not in merged:
            merged[source] = np.nan
        merged[target] = _single_quarter(merged, source)
        merged[target.replace("single", "ttm")] = merged.groupby(
            "symbol",
            sort=False,
        )[target].transform(lambda values: values.rolling(4, min_periods=4).sum())

    raw_price = _raw_price_on_or_before(bars, merged)
    grouped = merged.groupby("symbol", sort=False)
    merged["shares"] = merged.get("paid_in_capital")
    merged["price_at_available"] = raw_price
    merged["market_cap"] = merged["price_at_available"] * merged["shares"]
    merged["ep"] = merged["profit_ttm"] / merged["market_cap"]
    merged["bp"] = merged["equity_parent_company"] / merged["market_cap"]
    average_equity = (
        merged["equity_parent_company"] + grouped["equity_parent_company"].shift(4)
    ) / 2
    merged["roe"] = merged["profit_ttm"] / average_equity
    merged["gross_margin"] = merged["gross_profit_ttm"] / merged["revenue_ttm"]
    merged["leverage"] = merged["total_liabilities"] / merged["total_assets"]
    merged["profit_growth"] = grouped["profit_ttm"].pct_change(4, fill_method=None)
    merged["revenue_growth"] = grouped["revenue_ttm"].pct_change(4, fill_method=None)

    columns = [
        "quarter",
        "available_date",
        "symbol",
        "shares",
        "market_cap",
        *CANONICAL_FIELDS,
    ]
    output = merged[columns].replace([np.inf, -np.inf], np.nan)
    return (
        output.dropna(subset=list(CANONICAL_FIELDS), how="all")
        .sort_values(["available_date", "symbol", "quarter"])
        .reset_index(drop=True)
    )


def _quarter_parts(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["quarter"] = result["quarter"].astype(str).str.lower()
    result["year"] = pd.to_numeric(result["quarter"].str[:4], errors="coerce")
    result["quarter_no"] = pd.to_numeric(result["quarter"].str[-1], errors="coerce")
    return result.dropna(subset=["year", "quarter_no"])


def _single_quarter(frame: pd.DataFrame, column: str) -> pd.Series:
    cumulative = frame.groupby(["symbol", "year"], sort=False)[column]
    single = cumulative.diff()
    return single.where(frame["quarter_no"].ne(1), frame[column])


def _raw_price_on_or_before(bars: pd.DataFrame, rows: pd.DataFrame) -> pd.Series:
    price_column = "raw_close" if "raw_close" in bars else "close"
    prices = bars[["date", "symbol", price_column]].copy()
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices[price_column] = pd.to_numeric(prices[price_column], errors="coerce")
    prices = prices.dropna().sort_values(["date", "symbol"])
    left = rows[["available_date", "symbol"]].copy()
    left["_row"] = left.index
    left = left.sort_values(["available_date", "symbol"])
    matched = pd.merge_asof(
        left,
        prices,
        left_on="available_date",
        right_on="date",
        by="symbol",
        direction="backward",
    )
    return matched.set_index("_row")[price_column].reindex(rows.index)


def _empty() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "quarter",
            "available_date",
            "symbol",
            "shares",
            "market_cap",
            *CANONICAL_FIELDS,
        ]
    )


__all__ = [
    "BALANCE_FIELDS",
    "CANONICAL_FIELDS",
    "INCOME_FIELDS",
    "build_canonical_fundamentals",
    "first_disclosures",
]
