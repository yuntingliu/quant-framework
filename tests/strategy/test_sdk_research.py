from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

import alphalab.sdk.v1.research as research_module
from alphalab.portfolio import EqualWeightOptimizer
from alphalab.sdk.v1 import (
    BenchmarkDeviationConstraint,
    GroupWeightConstraint,
    PortfolioConstraintData,
    PortfolioConstraintError,
    PortfolioConstraintSet,
    PortfolioOptimizationError,
    RiskBudgetConstraint,
    RiskModelSpec,
    neutralize_factor_scores,
    optimize_portfolio,
)
from alphalab.sdk.v1.constraints import BenchmarkSnapshot, ClassificationSnapshot


def _returns() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    common = rng.normal(0.0002, 0.006, 180)
    return pd.DataFrame(
        {
            "A": common + rng.normal(0, 0.004, len(common)),
            "B": common + rng.normal(0, 0.008, len(common)),
            "C": common + rng.normal(0, 0.012, len(common)),
            "D": common + rng.normal(0, 0.016, len(common)),
        },
        index=pd.bdate_range(end="2026-06-30", periods=len(common)),
    )


def _constraint_inputs(
    *,
    tech_cap: float = 0.55,
    tech_risk_cap: float = 0.95,
    active_limit: float = 0.50,
    benchmark_weights: dict[str, float] | None = None,
) -> tuple[PortfolioConstraintSet, PortfolioConstraintData]:
    as_of = dt.date(2026, 6, 30)
    risk_model = RiskModelSpec(lookback=90, minimum_observations=60)
    constraint_set = PortfolioConstraintSet(
        constraints=(
            GroupWeightConstraint(
                id="tech_cap", classification="test_scheme:1", groups=("TECH",), upper=tech_cap
            ),
            BenchmarkDeviationConstraint(
                id="active_weight",
                benchmark="IDX",
                classification=None,
                max_abs_deviation=active_limit,
                max_staleness_days=0,
            ),
            RiskBudgetConstraint(
                id="tech_risk",
                classification="test_scheme:1",
                groups=("TECH",),
                upper=tech_risk_cap,
                risk_model=risk_model,
            ),
        )
    )
    data = PortfolioConstraintData(
        as_of=as_of,
        classifications={
            "test_scheme:1": ClassificationSnapshot(
                classification="test_scheme:1",
                effective_date=as_of,
                groups={"A": "TECH", "B": "TECH", "C": "FIN", "D": "FIN"},
                source_version="test-v1",
            )
        },
        benchmarks={
            "IDX": BenchmarkSnapshot(
                benchmark="IDX",
                effective_date=as_of,
                weights=benchmark_weights or {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25},
                source_version="test-v1",
            )
        },
    )
    return constraint_set, data


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


def test_optimizer_accepts_constraints_only_as_a_validated_pair() -> None:
    constraint_set, data = _constraint_inputs()
    with pytest.raises(TypeError, match="supplied together"):
        optimize_portfolio(method="equal_weight", symbols=["A", "B"], constraints=constraint_set)
    with pytest.raises(TypeError, match="supplied together"):
        optimize_portfolio(method="equal_weight", symbols=["A", "B"], constraint_data=data)


def test_all_optimizer_methods_return_constraint_compliant_weights() -> None:
    constraint_set, data = _constraint_inputs()
    for method in ("equal_weight", "minimum_variance", "risk_parity", "hrp", "max_sharpe"):
        arguments = {
            "method": method,
            "max_weight": 0.60,
            "minimum_observations": 60,
            "constraints": constraint_set,
            "constraint_data": data,
        }
        if method == "max_sharpe":
            arguments["expected_returns"] = {"A": 0.15, "B": 0.12, "C": 0.09, "D": 0.07}
        result = optimize_portfolio(_returns(), **arguments)
        checks = result.diagnostics["constraint_checks"]
        assert checks
        assert all(check["status"] == "pass" for check in checks)
        assert sum(result.weights.values()) == pytest.approx(1.0)


def test_equal_weight_projects_to_a_binding_group_constraint() -> None:
    constraint_set, data = _constraint_inputs(tech_cap=0.25)
    result = optimize_portfolio(
        _returns(),
        method="equal_weight",
        max_weight=0.60,
        constraints=constraint_set,
        constraint_data=data,
    )
    assert result.weights.get("A", 0.0) + result.weights.get("B", 0.0) <= 0.25 + 1e-8
    assert all(check["status"] == "pass" for check in result.diagnostics["constraint_checks"])


def test_equal_weight_projects_to_a_binding_risk_budget() -> None:
    constraint_set, data = _constraint_inputs(tech_risk_cap=0.30)
    result = optimize_portfolio(
        _returns(),
        method="equal_weight",
        max_weight=0.60,
        constraints=constraint_set,
        constraint_data=data,
    )
    risk_check = next(
        check for check in result.diagnostics["constraint_checks"] if check["constraint_id"] == "tech_risk"
    )
    assert risk_check["actual"] == pytest.approx(0.30, abs=1e-7)
    assert risk_check["status"] == "pass"


def test_equal_weight_projects_to_a_binding_benchmark_deviation() -> None:
    constraint_set, data = _constraint_inputs(
        active_limit=0.10,
        benchmark_weights={"A": 0.10, "B": 0.10, "C": 0.40, "D": 0.40},
    )
    result = optimize_portfolio(
        _returns(),
        method="equal_weight",
        max_weight=0.60,
        constraints=constraint_set,
        constraint_data=data,
    )
    checks = [
        check for check in result.diagnostics["constraint_checks"] if check["constraint_id"] == "active_weight"
    ]
    assert all(abs(check["actual"]) <= 0.10 + 1e-8 for check in checks)
    assert any(abs(check["actual"]) == pytest.approx(0.10, abs=1e-7) for check in checks)


def test_constraint_enforcement_respects_turnover_and_reports_infeasibility() -> None:
    constraint_set, data = _constraint_inputs()
    current = {"A": 0.4, "B": 0.4, "C": 0.1, "D": 0.1}
    with pytest.raises(PortfolioOptimizationError, match="optimization did not converge|infeasible"):
        optimize_portfolio(
            _returns(),
            method="minimum_variance",
            current_weights=current,
            max_weight=0.60,
            max_turnover=0.01,
            minimum_observations=60,
            constraints=constraint_set,
            constraint_data=data,
        )


def test_constraint_covariance_uses_only_returns_available_on_decision_date() -> None:
    constraint_set, data = _constraint_inputs()
    historical = _returns()
    future = pd.DataFrame(
        {symbol: [0.20, -0.20] for symbol in historical.columns},
        index=pd.bdate_range("2026-07-01", periods=2),
    )
    arguments = {
        "method": "equal_weight",
        "max_weight": 0.60,
        "constraints": constraint_set,
        "constraint_data": data,
    }
    baseline = optimize_portfolio(historical, **arguments)
    with_future_rows = optimize_portfolio(pd.concat([historical, future]), **arguments)
    assert with_future_rows.weights == pytest.approx(baseline.weights)
    assert with_future_rows.diagnostics["constraint_checks"] == baseline.diagnostics["constraint_checks"]


def test_constraint_aware_covariance_requires_dated_returns() -> None:
    constraint_set, data = _constraint_inputs()
    with pytest.raises(PortfolioConstraintError, match="requires returns with a date index"):
        optimize_portfolio(
            _returns().reset_index(drop=True),
            method="minimum_variance",
            max_weight=0.60,
            constraints=constraint_set,
            constraint_data=data,
        )
