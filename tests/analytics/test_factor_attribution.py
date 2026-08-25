from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from alphalab import ResultStore
from alphalab.analytics import factor_attribution


def test_factor_attribution_recovers_known_exposures_and_correlations():
    rng = np.random.default_rng(20260824)
    dates = pd.date_range("2020-01-31", periods=60, freq="ME")
    factors = pd.DataFrame(
        {
            "MKT": rng.normal(0.006, 0.04, len(dates)),
            "SMB": rng.normal(0.001, 0.025, len(dates)),
            "HML": rng.normal(0.001, 0.02, len(dates)),
            "MOM": rng.normal(0.002, 0.03, len(dates)),
            "RMW": rng.normal(0.001, 0.018, len(dates)),
            "rf": np.full(len(dates), 0.001),
        },
        index=dates,
    )
    strategy = (
        factors["rf"]
        + 0.003
        + 1.2 * (factors["MKT"] - factors["rf"])
        + 0.4 * factors["SMB"]
        - 0.3 * factors["HML"]
        + 0.2 * factors["MOM"]
        + 0.1 * factors["RMW"]
        + rng.normal(0.0, 0.001, len(dates))
    )
    executions = [
        {
            "turnover": 0.2,
            "factor_score_correlation": {
                "labels": ["value", "momentum"],
                "matrix": [[1.0, -0.25], [-0.25, 1.0]],
            },
        },
        {
            "turnover": 0.4,
            "factor_score_correlation": {
                "labels": ["value", "momentum"],
                "matrix": [[1.0, -0.15], [-0.15, 1.0]],
            },
        },
    ]

    result = factor_attribution(
        strategy,
        factors,
        executions=executions,
        research_thresholds={
            "min_sharpe": -100.0,
            "max_drawdown": -1.0,
            "max_turnover": 0.5,
        },
    )

    assert result["frequency"] == "monthly"
    assert result["observations"] == 60
    assert result["coverage"] == 1.0
    assert result["multi_factor"]["alpha_monthly"] == pytest.approx(0.003, abs=0.001)
    assert result["multi_factor"]["betas"] == pytest.approx(
        {"MKT": 1.2, "SMB": 0.4, "HML": -0.3, "MOM": 0.2, "RMW": 0.1},
        abs=0.03,
    )
    assert result["multi_factor"]["r_squared"] > 0.99
    assert result["factor_return_correlation"]["labels"] == [
        "MKT",
        "SMB",
        "HML",
        "MOM",
        "RMW",
    ]
    assert result["selection_score_correlation"] == {
        "labels": ["momentum", "value"],
        "periods": 2,
        "median_spearman": [[1.0, -0.2], [-0.2, 1.0]],
    }
    assert result["research_checks"]["passed"] is True
    assert len(result["input_snapshot"]) == 60


def test_factor_attribution_reports_insufficient_history_without_recomputing():
    dates = pd.date_range("2026-01-31", periods=3, freq="ME")
    returns = pd.Series([0.01, -0.02, 0.03], index=dates)
    factors = pd.DataFrame(
        {"MKT": [0.02, -0.01, 0.01], "rf": [0.001, 0.001, 0.001]},
        index=dates,
    )

    result = factor_attribution(returns, factors)

    assert result["capm"]["observations"] == 3
    assert result["capm"]["alpha_monthly"] is None
    assert any("At least 6" in warning for warning in result["warnings"])


def test_result_store_persists_the_frozen_attribution_snapshot(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    try:
        store.ensure_backtest_subject("project-1", "sqlite:pipeline_projects/project-1")
        snapshot = {"frequency": "monthly", "observations": 24, "capm": {"beta": 0.9}}
        backtest_id = store.save_backtest(
            pd.Series([0.01], index=pd.to_datetime(["2026-01-31"])),
            {"total_return": 0.01, "n_periods": 1},
            strategy_id="project-1",
            attribution=snapshot,
        )
        record = store.get_backtest_record(backtest_id)
    finally:
        store.close()

    assert json.loads(record["attribution_json"]) == snapshot
