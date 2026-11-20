"""Isolate default stores before test collection imports application modules."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# An autouse fixture would run too late: repository defaults are bound at import.
# Spawned strategy workers inherit these paths, as do unpatched API services.
_state = tempfile.TemporaryDirectory(prefix="alphalab-tests-", ignore_cleanup_errors=True)
_previous = {}
for _variable, _directory in (
    ("ALPHALAB_APP_DATA_DIR", "app"),
    ("ALPHALAB_CACHE_DIR", "cache"),
    ("ALPHALAB_RUNTIME_DIR", "runtime"),
):
    _previous[_variable] = os.environ.get(_variable)
    os.environ[_variable] = str(Path(_state.name) / _directory)


def pytest_unconfigure(config):
    for variable, value in _previous.items():
        if value is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = value
    _state.cleanup()


@pytest.fixture
def three_asset_constraint_case():
    """Shared portfolio-constraint fixture; numbers match contract section 14.5."""

    # Import inside the fixture: alphalab binds default store paths at import time.
    import datetime as dt

    import numpy as np

    from alphalab.sdk.v1 import (
        BenchmarkDeviationConstraint,
        GroupWeightConstraint,
        PortfolioConstraintData,
        PortfolioConstraintSet,
        RiskBudgetConstraint,
        RiskModelSpec,
    )
    from alphalab.sdk.v1.constraints import BenchmarkSnapshot, ClassificationSnapshot

    as_of = dt.date(2026, 6, 30)
    symbols = ("AAA", "BBB", "CCC")
    volatility = np.array([0.30, 0.25, 0.15])
    correlation = np.array([[1.0, 0.6, 0.2], [0.6, 1.0, 0.3], [0.2, 0.3, 1.0]])
    constraint_set = PortfolioConstraintSet(
        constraints=(
            GroupWeightConstraint(
                id="tech_cap", classification="test_scheme:1", groups=("TECH",), upper=0.60
            ),
            BenchmarkDeviationConstraint(
                id="industry_active",
                benchmark="IDX",
                classification="test_scheme:1",
                max_abs_deviation=0.05,
                max_staleness_days=31,
            ),
            RiskBudgetConstraint(
                id="tech_risk",
                classification="test_scheme:1",
                groups=("TECH",),
                upper=0.40,
                risk_model=RiskModelSpec(lookback=60, minimum_observations=40),
            ),
        )
    )
    data = PortfolioConstraintData(
        as_of=as_of,
        classifications={
            "test_scheme:1": ClassificationSnapshot(
                classification="test_scheme:1",
                effective_date=as_of,
                groups={"AAA": "TECH", "BBB": "TECH", "CCC": "FIN"},
                source_version="fixture-1",
            )
        },
        benchmarks={
            "IDX": BenchmarkSnapshot(
                benchmark="IDX",
                effective_date=as_of,
                weights={"AAA": 0.20, "BBB": 0.30, "CCC": 0.50},
                source_version="fixture-1",
            )
        },
    )
    return {
        "as_of": as_of,
        "symbols": symbols,
        "constraint_set": constraint_set,
        "data": data,
        # Target weights leave 5% cash; current holdings are the pre-trade book.
        "weights": {"AAA": 0.40, "BBB": 0.30, "CCC": 0.25},
        "current_weights": {"AAA": 0.25, "BBB": 0.25, "CCC": 0.45},
        "annual_covariance": np.outer(volatility, volatility) * correlation,
        "expected": {
            "group_weight": {"TECH": 0.70, "FIN": 0.25},
            "benchmark_deviation": {"TECH": 0.20, "FIN": -0.25},
            "risk_contribution": {"AAA": 0.5795, "BBB": 0.3323, "CCC": 0.0882},
            "tech_risk_contribution": 0.9118,
            "portfolio_volatility": 0.1890,
        },
    }
