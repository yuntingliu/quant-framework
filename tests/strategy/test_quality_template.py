from types import SimpleNamespace

import pandas as pd
import pytest

from alphalab.sdk.v1 import factor
from alphalab.strategy import get_factor_template


def test_quality_ranking_uses_complete_observations_and_low_leverage():
    inputs = pd.DataFrame({
        "roe": [0.2, 0.1, 100.0, float("inf")],
        "roa": [0.1, 0.05, None, 0.5],
        "leverage": [0.2, 0.8, 0.0, 0.0],
    }, index=["HIGH", "LOW", "MISSING", "INVALID"])
    namespace = {"factor": factor}
    exec(get_factor_template("quality_profitability").source, namespace)
    context = SimpleNamespace(fundamental=lambda field: inputs[field])
    values = namespace["quality_profitability"](context)
    assert values["HIGH"] == pytest.approx(1.)
    assert values["LOW"] == pytest.approx(.5)
    assert values[["MISSING", "INVALID"]].isna().all()
