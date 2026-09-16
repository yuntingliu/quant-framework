import numpy as np
import pandas as pd
import pytest

from alphalab.dataio import build_canonical_fundamentals, first_disclosures


def financials():
    quarters = ["2018q1", "2018q4", "2019q1", "2019q4", "2020q1"]
    dates = ["2018-04-20", "2019-03-20", "2019-04-20", "2020-03-20", "2020-04-20"]
    common = {"symbol": ["A"] * 5, "quarter": quarters, "info_date": dates, "if_adjusted": [0] * 5}
    income = pd.DataFrame({**common, "net_profit_parent_company": [10, 50, 15, 70, 20],
                           "revenue": [100, 500, 150, 700, 200]})
    balance = pd.DataFrame({**common, "equity_parent_company": [100, 120, 140, 160, 180],
                            "total_assets": [200, 240, 280, 320, 360], "total_liabilities": [100] * 5,
                            "paid_in_capital": [10] * 5})
    return income, balance


def test_ttm_and_average_equity_use_exact_periods_not_four_observed_rows():
    income, balance = financials()
    result = build_canonical_fundamentals(income, balance, pd.DataFrame())
    row = result.loc[result.quarter.eq("2020q1")].iloc[0]
    assert row.profit_ttm == 75  # 20 + 70 - 15, despite missing Q2 and Q3.
    assert row.roe == pytest.approx(75 / 160)  # Mean of 2019Q1 and 2020Q1 equity.
    assert row.roa == pytest.approx(75 / 320)
    assert row.roe_latest_equity == pytest.approx(75 / 180)
    assert row.profit_growth == pytest.approx(75 / 55 - 1)
    assert row.available_date == pd.Timestamp("2020-04-21")


def test_missing_same_quarter_or_late_dependency_cannot_be_borrowed():
    income, balance = financials()
    income.loc[income.quarter.eq("2019q4"), "info_date"] = "2021-04-20"
    result = build_canonical_fundamentals(income, balance, pd.DataFrame(), asof_date="2020-05-01")
    assert np.isnan(result.loc[result.quarter.eq("2020q1"), "roe"].iloc[0])
    income, balance = financials()
    balance = balance.loc[balance.quarter.ne("2019q1")]
    row = build_canonical_fundamentals(income, balance, pd.DataFrame()).query("quarter == '2020q1'").iloc[0]
    assert row.profit_ttm == 75
    assert np.isnan(row.roe)


def test_negative_equity_and_loss_base_are_not_quality_or_growth():
    income, balance = financials()
    balance.loc[balance.quarter.eq("2019q1"), "equity_parent_company"] = -500
    income.loc[income.quarter.eq("2018q4"), "net_profit_parent_company"] = -100
    row = build_canonical_fundamentals(income, balance, pd.DataFrame()).query("quarter == '2020q1'").iloc[0]
    assert np.isnan(row.roe)
    assert np.isnan(row.profit_growth)


def test_adjusted_only_batch_is_not_silently_treated_as_original():
    income, _ = financials()
    income["if_adjusted"] = 1
    assert first_disclosures(income).empty


def test_publication_day_is_excluded_and_future_rows_do_not_change_history():
    income, balance = financials()
    before = build_canonical_fundamentals(income, balance, pd.DataFrame(), asof_date="2020-04-20")
    assert "2020q1" not in set(before.quarter)
    extra = income.iloc[-1:].copy()
    extra["quarter"], extra["info_date"], extra["net_profit_parent_company"] = "2020q2", "2020-08-20", 9999
    after = build_canonical_fundamentals(pd.concat([income, extra]), balance, pd.DataFrame(), asof_date="2020-04-20")
    pd.testing.assert_frame_equal(before, after)
