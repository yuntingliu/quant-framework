"""Sparse research fields retain SDK history semantics when windowing is optimized."""

import pandas as pd
import pytest

from alphalab.sdk.v1 import Event, FactorContext


@pytest.mark.parametrize("fields", ["close", "roe", ["close", "roe"]])
@pytest.mark.parametrize("window", [1, 2, 20])
def test_sparse_history_matches_full_pivot_and_is_isolated(fields, window):
    bars = pd.DataFrame({
        "date": pd.to_datetime(["2020-01-01", "2020-01-01", "2020-01-02", "2020-01-03",
                                "2020-01-03", "2020-01-04", "2020-01-05"]),
        "symbol": ["A", "B", "A", "A", "B", "A", "A"],
        "close": [1., 2., 3., None, 5., None, 999.],
        "roe": [None, .2, None, .4, None, None, 999.],
    })
    requested = ["B", "A", "MISSING"]
    context = FactorContext(event=Event.SESSION_CLOSE, as_of="2020-01-04",
                            sessions=bars.date.unique(), symbols=requested, bars=bars)
    names = [fields] if isinstance(fields, str) else fields
    visible = bars.loc[bars.date.le("2020-01-04")].sort_values("date")
    pieces = {name: visible.pivot_table(index="date", columns="symbol", values=name, aggfunc="last")
              .reindex(columns=requested).tail(window) for name in names}
    expected = pieces[names[0]] if len(names) == 1 else pd.concat(pieces, axis=1)
    actual = context.history(fields, window=window)
    pd.testing.assert_frame_equal(actual, expected)
    actual.iloc[:, :] = -100
    pd.testing.assert_frame_equal(context.history(fields, window=window), expected)
    if isinstance(fields, str):
        duplicate = context.history([fields, fields], window=window)
        pd.testing.assert_frame_equal(duplicate, pd.concat({fields: expected}, axis=1))
    # Current means the latest observed row, including its missing value.
    assert pd.isna(context.current("close")["A"])
    assert context.current("close")["B"] == 5.
