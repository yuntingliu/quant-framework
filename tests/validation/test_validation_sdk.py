from __future__ import annotations

import ast

import numpy as np
import pandas as pd
import pytest

from alphalab.strategy.repository import StrategyRepository
from alphalab.validation import repository as validation_repository_module
from alphalab.validation.builtins import DEFAULT_VALIDATION_SOURCE
from alphalab.validation.repository import ValidationRepository
from alphalab.validation.runtime import ValidationRuntimeError, execute_validation
from alphalab.validation.source import (
    ValidationSourceError,
    inspect_validation_source,
    update_validation_parameters,
)
from dashboard.backend.services import validation_service


def test_default_validation_source_exposes_visible_metrics_and_alpha_beta() -> None:
    inspection = inspect_validation_source(DEFAULT_VALIDATION_SOURCE)

    assert [item.id for item in inspection.entrypoints] == [
        "performance",
        "alpha_beta",
        "risk",
        "research_quality",
    ]
    assert "np.linalg.lstsq" in DEFAULT_VALIDATION_SOURCE
    assert "_newey_west_covariance" in DEFAULT_VALIDATION_SOURCE
    tree = ast.parse(DEFAULT_VALIDATION_SOURCE)
    assert ast.get_docstring(tree)
    assert all(
        ast.get_docstring(node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    )
    assert [item.name for item in inspection.entrypoints[0].parameters] == [
        "periods_per_year",
        "risk_free_rate",
    ]


def test_validation_parameter_edit_changes_only_the_python_default() -> None:
    updated, inspection = update_validation_parameters(
        DEFAULT_VALIDATION_SOURCE,
        [{"entrypoint_id": "alpha_beta", "parameter": "newey_west_lags", "value": 5}],
    )

    assert "newey_west_lags: int = 5" in updated
    assert "coefficients, _, rank, _ = np.linalg.lstsq" in updated
    entrypoint = next(item for item in inspection.entrypoints if item.id == "alpha_beta")
    assert next(item.default for item in entrypoint.parameters if item.name == "newey_west_lags") == 5


def test_immutable_builtin_validation_advances_with_official_template(
    tmp_path, monkeypatch,
) -> None:
    database = tmp_path / "builtin-validation-refresh.db"
    strategy = StrategyRepository(database)
    strategy.close()
    legacy_source = DEFAULT_VALIDATION_SOURCE.replace(
        '    """计算回测编辑器下方展示的收益、风险与样本数指标。"""\n',
        "",
        1,
    )
    monkeypatch.setattr(
        validation_repository_module,
        "DEFAULT_VALIDATION_SOURCE",
        legacy_source,
    )
    seeded = ValidationRepository(database)
    seeded.get_or_create("sdk-v1-default")
    seeded.close()

    monkeypatch.setattr(
        validation_repository_module,
        "DEFAULT_VALIDATION_SOURCE",
        DEFAULT_VALIDATION_SOURCE,
    )
    refreshed = ValidationRepository(database)
    try:
        current = refreshed.get_or_create("sdk-v1-default")
        assert current["current_revision"] == 2
        assert current["source"] == DEFAULT_VALIDATION_SOURCE
        assert refreshed.get_package("sdk-v1-default", 1)["source"] == legacy_source
        assert refreshed.get_package("sdk-v1-default", 2)["source"] == DEFAULT_VALIDATION_SOURCE
    finally:
        refreshed.close()


def test_validation_repository_clones_and_pins_independent_source(tmp_path) -> None:
    db_path = tmp_path / "validation.db"
    strategies = StrategyRepository(db_path)
    try:
        strategies.clone_project("sdk-v1-default", "research-copy", name="Research copy")
    finally:
        strategies.close()
    repository = ValidationRepository(db_path)
    try:
        original = repository.get_or_create("research-copy")
        updated = repository.update_parameters(
            "research-copy",
            [{"entrypoint_id": "performance", "parameter": "periods_per_year", "value": 365}],
            expected_source_sha256=original["source_sha256"],
        )
        pinned = repository.get_package("research-copy", 1)
    finally:
        repository.close()

    assert updated["current_revision"] == 2
    assert "periods_per_year: int = 365" in updated["source"]
    assert "periods_per_year: int = 252" in pinned["source"]


def test_user_project_requires_explicit_default_validation_migration(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "validation-migration.db"
    strategies = StrategyRepository(db_path)
    try:
        strategies.clone_project("sdk-v1-default", "older-user-project", name="Older")
    finally:
        strategies.close()
    repository = ValidationRepository(db_path)
    try:
        original = repository.get_or_create("older-user-project")
        older = repository.update_parameters(
            "older-user-project",
            [{"entrypoint_id": "performance", "parameter": "periods_per_year", "value": 365}],
            expected_source_sha256=original["source_sha256"],
        )
    finally:
        repository.close()

    monkeypatch.setattr(validation_service, "repository", lambda: ValidationRepository(db_path))
    unchanged = validation_service.get_workspace("older-user-project")
    migrated = validation_service.migrate_to_default(
        "older-user-project",
        expected_source_sha256=unchanged["source_sha256"],
    )

    assert unchanged["current_revision"] == older["current_revision"] == 2
    assert "periods_per_year: int = 365" in unchanged["source"]
    assert migrated["current_revision"] == 3
    assert migrated["source"] == DEFAULT_VALIDATION_SOURCE


def test_validation_runtime_returns_json_outputs_from_local_python() -> None:
    dates = pd.date_range("2022-01-03", periods=520, freq="B")
    returns = pd.Series(np.linspace(-0.005, 0.006, len(dates)), index=dates)
    months = pd.date_range("2022-01-31", periods=24, freq="ME")
    factors = pd.DataFrame(
        {
            "MKT": np.linspace(-0.03, 0.04, len(months)),
            "SMB": np.linspace(0.02, -0.01, len(months)),
            "HML": np.linspace(-0.01, 0.02, len(months)),
            "MOM": np.linspace(0.01, 0.03, len(months)),
            "RMW": np.linspace(-0.02, 0.01, len(months)),
            "rf": np.full(len(months), 0.002),
        },
        index=months,
    )

    outputs = execute_validation(
        DEFAULT_VALIDATION_SOURCE,
        returns=returns,
        benchmark_returns=returns * 0.8,
        weights=pd.DataFrame(index=dates),
        factor_returns=factors,
        executions=[],
        settings={},
    )

    assert outputs["performance"]["n_periods"] == 520
    assert outputs["alpha_beta"]["observations"] == 24
    assert "MKT" in outputs["alpha_beta"]["capm"]["betas"]
    assert outputs["risk"]["status"] == "sufficient"
    assert outputs["risk"]["var_95"] is not None
    assert outputs["research_quality"]["status"] == "insufficient"


def test_validation_uses_frozen_run_diagnostics_and_never_zero_fills_tail_risk() -> None:
    dates = pd.date_range("2024-01-02", periods=12, freq="B")
    evidence = [
        {"ic": 0.03, "coverage": 0.80, "signal_date": str(date)[:10]}
        for date in dates
    ]
    outputs = execute_validation(
        DEFAULT_VALIDATION_SOURCE,
        returns=pd.Series(np.linspace(-0.01, 0.01, len(dates)), index=dates),
        benchmark_returns=pd.Series(0.0, index=dates),
        weights=pd.DataFrame({"A": 0.6, "B": 0.4}, index=dates),
        factor_returns=pd.DataFrame(),
        executions=[],
        settings={},
        run_diagnostics={
            "research_valid": True,
            "research_invalid_reasons": [],
            "execution_fidelity": {"mean": 0.95},
            "signal_evidence": {"rows": evidence},
            "execution_data_exclusions": {"symbol_date_count": 0},
        },
    )

    assert outputs["risk"]["status"] == "insufficient"
    assert outputs["risk"]["var_95"] is None
    assert outputs["risk"]["cvar_95"] is None
    assert outputs["research_quality"]["status"] == "pass"
    assert outputs["research_quality"]["mean_rank_ic"] == pytest.approx(0.03)


def test_validation_contract_and_output_boundary_reject_invalid_code() -> None:
    with pytest.raises(ValidationSourceError, match="alpha_beta"):
        inspect_validation_source(
            "VALIDATION_SDK_VERSION = 1\n"
            "from alphalab.validation_sdk import analysis\n"
            "@analysis(id='performance')\n"
            "def performance(context): return {}\n"
        )

    invalid_output = DEFAULT_VALIDATION_SOURCE.replace(
        '"n_periods": int(len(returns)),',
        '"n_periods": pd.DataFrame({"not": ["json"]}),',
    )
    dates = pd.date_range("2024-01-01", periods=5, freq="B")
    with pytest.raises(ValidationRuntimeError, match="JSON-compatible"):
        execute_validation(
            invalid_output,
            returns=pd.Series(0.0, index=dates),
            benchmark_returns=pd.Series(0.0, index=dates),
            weights=pd.DataFrame(index=dates),
            factor_returns=pd.DataFrame(),
            executions=[],
            settings={},
        )
