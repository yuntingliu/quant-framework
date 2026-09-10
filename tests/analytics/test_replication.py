import numpy as np
import pandas as pd
import pytest

from alphalab.analytics import audit_annual_return_table


def test_compounding_and_win_rate_are_separate_from_arithmetic_mean():
    frame = pd.DataFrame({"benchmark": [0.0, 0.0], "factor": [0.1, -0.1]}, index=[2020, 2021])
    actual = audit_annual_return_table(frame, benchmark="benchmark")
    assert actual.loc["factor", "cumulative_return"] == pytest.approx(-0.01)
    assert actual.loc["factor", "calendar_cagr"] == pytest.approx(np.sqrt(0.99) - 1)
    assert actual.loc["factor", "annual_win_rate_vs_benchmark"] == 0.5
    assert np.isnan(actual.loc["benchmark", "annual_win_rate_vs_benchmark"])


@pytest.mark.parametrize("years,values", [([2020, 2020], [0, 0]), ([2020, 2022], [0, 0]),
                                         ([2020, 2021], [0, np.nan]), ([2020, 2021], [0, -1.1])])
def test_incomplete_or_invalid_years_cannot_silently_change_cagr(years, values):
    with pytest.raises(ValueError):
        audit_annual_return_table(pd.DataFrame({"benchmark": values}, index=years), benchmark="benchmark")
