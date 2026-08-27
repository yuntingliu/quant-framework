from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alphalab.strategy.repository import StrategyRepository
from alphalab.validation.builtins import DEFAULT_VALIDATION_SOURCE
from alphalab.validation.repository import ValidationRepository
from alphalab.validation.runtime import ValidationRuntimeError, execute_validation
from alphalab.validation.source import (
    ValidationSourceError,
    inspect_validation_source,
    update_validation_parameters,
)


def test_default_validation_source_exposes_visible_metrics_and_alpha_beta() -> None:
    inspection = inspect_validation_source(DEFAULT_VALIDATION_SOURCE)

    assert [item.id for item in inspection.entrypoints] == ["performance", "alpha_beta"]
    assert "np.linalg.lstsq" in DEFAULT_VALIDATION_SOURCE
    assert "_newey_west_covariance" in DEFAULT_VALIDATION_SOURCE
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
