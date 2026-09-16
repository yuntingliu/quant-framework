from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import alphalab.sdk.v1.research as research_module
from alphalab.portfolio import EqualWeightOptimizer
from alphalab.sdk.v1 import (
    PortfolioOptimizationError,
    neutralize_factor_scores,
    optimize_portfolio,
)


def _returns() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    common = rng.normal(0.0002, 0.006, 180)
    return pd.DataFrame(
        {
            "A": common + rng.normal(0, 0.004, len(common)),
            "B": common + rng.normal(0, 0.008, len(common)),
            "C": common + rng.normal(0, 0.012, len(common)),
            "D": common + rng.normal(0, 0.016, len(common)),
        }
    )


def test_neutralize_factor_scores_removes_contemporaneous_exposure() -> None:
    symbols = [f"S{index:03d}" for index in range(40)]
    exposure = pd.Series(np.linspace(-2, 2, len(symbols)), index=symbols)
    noise = pd.Series(np.sin(np.arange(len(symbols))) * 0.05, index=symbols)
    result = neutralize_factor_scores(
        3.0 * exposure + noise,
        pd.DataFrame({"size": exposure}),
    )

    assert abs(result.values.corr(exposure)) < 1e-10
    assert result.diagnostics["observations"] == 40
    assert result.diagnostics["r_squared"] > 0.99


def test_optimizer_methods_are_deterministic_and_core_compatible() -> None:
    returns = _returns()
    for method in ("minimum_variance", "risk_parity", "hrp"):
        first = optimize_portfolio(
            returns,
            method=method,
            max_weight=0.45,
            minimum_observations=60,
        )
        second = optimize_portfolio(
            returns,
            method=method,
            max_weight=0.45,
            minimum_observations=60,
        )
        assert dict(first.weights) == pytest.approx(dict(second.weights), abs=1e-10)
        assert sum(first.weights.values()) == pytest.approx(1.0)
        assert min(first.weights.values()) >= 0
        assert max(first.weights.values()) <= 0.45 + 1e-8
        assert first.diagnostics["converged"] is True


def test_risk_parity_equalizes_risk_contributions() -> None:
    result = optimize_portfolio(
        _returns(), method="risk_parity", max_weight=0.60, minimum_observations=60
    )
    contributions = np.array(
        [value for value in result.diagnostics["risk_contributions"].values()]
    )

    assert contributions.std() < 0.01


def test_optimizer_enforces_turnover_and_rejects_infeasible_requests() -> None:
    returns = _returns()
    current = {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
    constrained = optimize_portfolio(
        returns,
        method="minimum_variance",
        current_weights=current,
        max_weight=0.40,
        max_turnover=0.05,
        minimum_observations=60,
    )
    assert constrained.diagnostics["expected_turnover"] <= 0.05 + 1e-8
    covariance = returns.cov().to_numpy()
    before = np.array([current[symbol] for symbol in returns.columns])
    after = np.array([constrained.weights.get(symbol, 0.0) for symbol in returns.columns])
    assert after @ covariance @ after < before @ covariance @ before

    with pytest.raises(PortfolioOptimizationError, match="infeasible"):
        optimize_portfolio(method="equal_weight", symbols=["A", "B"], max_weight=0.40)
    with pytest.raises(PortfolioOptimizationError, match="explicit expected_returns"):
        optimize_portfolio(
            returns, method="max_sharpe", max_weight=0.60, minimum_observations=60
        )
    with pytest.raises(PortfolioOptimizationError, match="infeasible"):
        optimize_portfolio(
            returns,
            method="minimum_variance",
            current_weights={"OUTSIDE": 1.0},
            max_turnover=0.20,
            minimum_observations=60,
        )


def test_zero_turnover_preserves_existing_unequal_weights() -> None:
    result = optimize_portfolio(
        method="equal_weight", symbols=["A", "B"],
        current_weights={"A": 0.7, "B": 0.3}, max_weight=0.8, max_turnover=0.0,
    )
    assert result.weights == pytest.approx({"A": 0.7, "B": 0.3}, abs=1e-8)
    assert result.diagnostics["expected_turnover"] <= 1e-8


def test_turnover_budget_counts_cash_and_exited_holdings() -> None:
    arguments = dict(
        method="equal_weight", symbols=["A", "B"], target_gross=0.8,
        current_weights={"A": 0.3, "B": 0.3, "OUTSIDE": 0.1}, max_weight=0.5,
    )
    result = optimize_portfolio(**arguments, max_turnover=0.2)
    assert result.weights == pytest.approx({"A": 0.4, "B": 0.4}, abs=1e-8)
    assert result.diagnostics["expected_turnover"] == pytest.approx(0.2)
    with pytest.raises(PortfolioOptimizationError):
        optimize_portfolio(**arguments, max_turnover=0.15)


def test_max_sharpe_requires_and_uses_explicit_forecasts() -> None:
    result = optimize_portfolio(
        _returns(),
        method="max_sharpe",
        expected_returns={"A": 0.15, "B": 0.10, "C": 0.07, "D": 0.04},
        max_weight=0.60,
        minimum_observations=60,
    )

    assert sum(result.weights.values()) == pytest.approx(1.0)
    assert result.weights.get("A", 0.0) > result.weights.get("D", 0.0)


def test_legacy_equal_weight_helper_does_not_break_its_own_cap() -> None:
    with pytest.raises(ValueError, match="infeasible"):
        EqualWeightOptimizer(max_weight=0.10).optimize(["A", "B", "C", "D", "E"])


def test_equal_weight_keeps_cash_and_does_not_require_return_history() -> None:
    result = optimize_portfolio(
        pd.DataFrame({"A": [0.01], "B": [0.02], "C": [np.nan]}),
        method="equal_weight",
        target_gross=0.75,
        max_weight=0.30,
    )

    assert set(result.weights) == {"A", "B", "C"}
    assert sum(result.weights.values()) == pytest.approx(0.75)
    assert max(result.weights.values()) <= 0.30
    assert result.diagnostics["observations"] == 1


def test_singular_covariance_is_repaired_and_short_history_is_rejected() -> None:
    base = np.linspace(-0.01, 0.01, 80)
    singular = pd.DataFrame({"A": base, "B": base, "C": base * 2})

    result = optimize_portfolio(
        singular,
        method="minimum_variance",
        max_weight=0.60,
        minimum_observations=60,
    )

    assert result.diagnostics["covariance_adjusted"] is True
    with pytest.raises(PortfolioOptimizationError, match="at least 60 complete"):
        optimize_portfolio(
            singular.iloc[:20], method="risk_parity", minimum_observations=60
        )


def test_solver_failure_is_explicit_and_never_falls_back(monkeypatch) -> None:
    class FailedResult:
        success = False
        message = "synthetic solver failure"
        x = np.array([])

    monkeypatch.setattr(research_module, "minimize", lambda *_args, **_kwargs: FailedResult())

    with pytest.raises(PortfolioOptimizationError, match="synthetic solver failure"):
        optimize_portfolio(
            _returns(), method="minimum_variance", minimum_observations=60
        )
