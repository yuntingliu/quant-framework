"""Point-in-time financial statement transforms for generic strategies."""
from __future__ import annotations

import numpy as np
import pandas as pd

CANONICAL_FIELDS = (
    "ep",
    "bp",
    "roe",
    "roa",
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
    result["info_date"] = pd.to_datetime(result["info_date"].astype(str), errors="coerce")
    result["quarter"] = result["quarter"].astype(str).str.lower()
    result = result.loc[result["quarter"].str.fullmatch(r"\d{4}q[1-4]")]
    if "if_adjusted" in result:
        # An adjusted-only input is not an original vintage. Its presence must
        # not be accepted or rejected depending on unrelated symbols in the batch.
        original = pd.to_numeric(result["if_adjusted"], errors="coerce").eq(0)
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
    disclosure_lag_days: int = 1,
) -> pd.DataFrame:
    """Build PIT fields using exact reporting quarters and available dependencies.

    Date-only disclosures become usable on the next calendar day by default.
    Missing quarters never become adjacent observations in a rolling calculation.
    Adjusted-only histories are excluded, rather than relabelled as original.
    """

    if isinstance(disclosure_lag_days, bool) or not isinstance(disclosure_lag_days, int) or disclosure_lag_days < 0:
        raise ValueError("disclosure_lag_days must be a non-negative integer")

    income_first = first_disclosures(income)
    balance_first = first_disclosures(balance)
    if income_first.empty or balance_first.empty:
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
    merged["statement_date"] = merged["available_date"]
    merged["available_date"] += pd.Timedelta(days=disclosure_lag_days)
    if asof_date is not None:
        merged = merged.loc[merged["available_date"].le(pd.Timestamp(asof_date))]
    merged = _quarter_parts(merged).sort_values(["symbol", "year", "quarter_no"])
    for column in (*INCOME_FIELDS, *BALANCE_FIELDS):
        merged[column] = pd.to_numeric(merged.get(column, np.nan), errors="coerce")

    # Use income rows directly: a missing balance for an intermediate quarter
    # must not destroy otherwise observable TTM profit.
    income_lookup = _quarter_parts(income_first.rename(columns={"info_date": "income_date"}))
    for name in INCOME_FIELDS:
        income_lookup[name] = pd.to_numeric(income_lookup.get(name, np.nan), errors="coerce")
    income_lookup["revenue_base"] = income_lookup.revenue.where(income_lookup.revenue.notna(), income_lookup.operating_revenue)
    income_lookup["gross_profit_base"] = income_lookup.gross_profit.where(income_lookup.gross_profit.notna(),
        income_lookup.revenue_base - income_lookup.cost_of_goods_sold)
    balance_lookup = _quarter_parts(balance_first.rename(columns={"info_date": "balance_date"}))
    for name in BALANCE_FIELDS:
        balance_lookup[name] = pd.to_numeric(balance_lookup.get(name, np.nan), errors="coerce")

    def lookup(table: pd.DataFrame, column: str, periods: pd.Series, date_column: str) -> pd.Series:
        """Read exact quarters only if that dependency was already disclosed."""
        indexed = table.assign(period=table.year * 4 + table.quarter_no - 1).set_index(["symbol", "period"])
        keys = pd.MultiIndex.from_arrays([merged.symbol, periods])
        values = pd.to_numeric(indexed[column], errors="coerce").reindex(keys)
        dates = pd.to_datetime(indexed[date_column]).reindex(keys)
        result = pd.Series(values.to_numpy(), index=merged.index, dtype=float)
        visible = pd.Series(dates.to_numpy(), index=merged.index).le(merged.statement_date)
        return result.where(visible)

    periods = merged.year * 4 + merged.quarter_no - 1

    def ttm(column: str, target_periods: pd.Series) -> pd.Series:
        current = lookup(income_lookup, column, target_periods, "income_date")
        prior_year_end = (target_periods // 4) * 4 - 1
        trailing = current + lookup(income_lookup, column, prior_year_end, "income_date") - lookup(
            income_lookup, column, target_periods - 4, "income_date"
        )
        return trailing.where(target_periods.mod(4).ne(3), current)

    for column, target in (("revenue_base", "revenue_ttm"), ("gross_profit_base", "gross_profit_ttm"),
                           ("net_profit_parent_company", "profit_ttm")):
        merged[target] = ttm(column, periods)

    raw_price = _raw_price_on_or_before(bars, merged)
    merged["shares"] = merged.get("paid_in_capital")
    merged["price_at_available"] = raw_price
    merged["market_cap"] = merged["price_at_available"] * merged["shares"]
    merged["ep"] = merged["profit_ttm"] / merged["market_cap"]
    merged["bp"] = merged["equity_parent_company"] / merged["market_cap"]
    previous_equity = lookup(balance_lookup, "equity_parent_company", periods - 4, "balance_date")
    previous_assets = lookup(balance_lookup, "total_assets", periods - 4, "balance_date")
    equity = merged.equity_parent_company
    average_equity = ((equity + previous_equity) / 2).where(equity.gt(0) & previous_equity.gt(0))
    average_assets = ((merged.total_assets + previous_assets) / 2).where(merged.total_assets.gt(0) & previous_assets.gt(0))
    merged["roe"] = merged.profit_ttm / average_equity
    merged["roe_latest_equity"] = merged.profit_ttm / equity.where(equity.gt(0))
    merged["roa"] = merged.profit_ttm / average_assets
    merged["gross_margin"] = merged.gross_profit_ttm / merged.revenue_ttm.where(merged.revenue_ttm.gt(0))
    merged["leverage"] = merged.total_liabilities / merged.total_assets.where(merged.total_assets.gt(0))
    for column, target, previous in (("net_profit_parent_company", "profit_growth", "profit_ttm"),
                                      ("revenue_base", "revenue_growth", "revenue_ttm")):
        prior_ttm = ttm(column, periods - 4)
        # A loss-to-profit reversal is not ordinary percentage growth.
        merged[target] = merged[previous] / prior_ttm.where(prior_ttm.gt(0)) - 1
    merged["book_equity"] = equity

    columns = [
        "quarter",
        "available_date",
        "symbol",
        "shares",
        "market_cap",
        "profit_ttm",
        "revenue_ttm",
        "book_equity",
        "roe_latest_equity",
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


def _raw_price_on_or_before(bars: pd.DataFrame, rows: pd.DataFrame) -> pd.Series:
    if bars.empty or rows.empty:
        return pd.Series(np.nan, index=rows.index, dtype=float)
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
            "profit_ttm",
            "revenue_ttm",
            "book_equity",
            "roe_latest_equity",
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
