"""Bounded local-Python execution for a pinned validation.py package."""

from __future__ import annotations

import json
import math
import pickle
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from alphalab.validation.source import inspect_validation_source

MAX_VALIDATION_OUTPUT_BYTES = 2_000_000
DEFAULT_VALIDATION_TIMEOUT_SECONDS = 30


class ValidationRuntimeError(RuntimeError):
    pass


def execute_validation(
    source: str,
    *,
    returns: pd.Series,
    benchmark_returns: pd.Series,
    weights: pd.DataFrame,
    factor_returns: pd.DataFrame,
    executions: list[dict] | tuple[dict, ...],
    settings: Mapping[str, Any],
    diagnostics: Mapping[str, Any] | None = None,
    timeout_seconds: int = DEFAULT_VALIDATION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute trusted project code outside the API process.

    This isolates crashes and supplies a timeout/output boundary. It is not an
    OS security sandbox; the UI must continue to require local-Python consent.
    """

    inspection = inspect_validation_source(source)
    payload = {
        "source": source,
        "returns": pd.Series(returns).copy(),
        "benchmark_returns": pd.Series(benchmark_returns).copy(),
        "weights": pd.DataFrame(weights).copy(),
        "factor_returns": pd.DataFrame(factor_returns).copy(),
        "executions": list(executions),
        "settings": dict(settings),
        "diagnostics": dict(diagnostics or {}),
        "entrypoints": [
            {"id": item.id, "function": item.function} for item in inspection.entrypoints
        ],
        "max_output_bytes": MAX_VALIDATION_OUTPUT_BYTES,
    }
    with tempfile.TemporaryDirectory(prefix="alphalab-validation-") as temporary:
        root = Path(temporary)
        input_path = root / "input.pkl"
        output_path = root / "output.json"
        input_path.write_bytes(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alphalab.validation.worker",
                    str(input_path),
                    str(output_path),
                ],
                cwd=Path(__file__).resolve().parents[2],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(1, min(int(timeout_seconds), 300)),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValidationRuntimeError(
                f"validation.py exceeded the {timeout_seconds}s execution limit"
            ) from exc
        if not output_path.exists():
            raise ValidationRuntimeError(
                f"validation.py process exited without a result (exit code {completed.returncode})"
            )
        if output_path.stat().st_size > MAX_VALIDATION_OUTPUT_BYTES:
            raise ValidationRuntimeError("validation.py output exceeded 2000000 bytes")
        try:
            message = json.loads(output_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValidationRuntimeError("validation.py returned an invalid result envelope") from exc
    if not message.get("ok"):
        raise ValidationRuntimeError(str(message.get("error") or "validation.py failed"))
    outputs = message.get("outputs")
    if not isinstance(outputs, dict):
        raise ValidationRuntimeError("validation.py must return named JSON-compatible outputs")
    for required in ("performance", "alpha_beta"):
        if not isinstance(outputs.get(required), dict):
            raise ValidationRuntimeError(f"@analysis(id={required!r}) must return a dictionary")
    _validate_performance_output(outputs["performance"])
    _validate_attribution_output(outputs["alpha_beta"])
    if "research_quality" in outputs:
        _validate_research_quality_output(outputs["research_quality"])
    return outputs


def _validate_research_quality_output(output: Any) -> None:
    if not isinstance(output, dict) or not isinstance(output.get("passed"), bool):
        raise ValidationRuntimeError("research_quality.passed must be a boolean")
    if len(json.dumps(output, ensure_ascii=False).encode("utf-8")) > 16_000:
        raise ValidationRuntimeError("research_quality must be compact evidence under 16000 bytes")
    for name in ("reasons", "warnings"):
        values = output.get(name)
        if not isinstance(values, list) or len(values) > 50 or not all(
            isinstance(value, str) and 0 < len(value) <= 1000 for value in values
        ):
            raise ValidationRuntimeError(f"research_quality.{name} must be a bounded list of strings")
    if output["passed"] == bool(output["reasons"]):
        raise ValidationRuntimeError("research_quality.passed must agree with its failure reasons")


def _validate_performance_output(output: dict[str, Any]) -> None:
    for name in ("total_return", "annual_return", "annual_vol", "sharpe", "max_drawdown"):
        value = output.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValidationRuntimeError(
                f"performance.{name} must be a finite number"
            )
    periods = output.get("n_periods")
    if isinstance(periods, bool) or not isinstance(periods, int) or periods < 0:
        raise ValidationRuntimeError("performance.n_periods must be a non-negative integer")


def _validate_attribution_output(output: dict[str, Any]) -> None:
    observations = output.get("observations")
    if isinstance(observations, bool) or not isinstance(observations, int) or observations < 0:
        raise ValidationRuntimeError("alpha_beta.observations must be a non-negative integer")
    coverage = output.get("coverage")
    if isinstance(coverage, bool) or not isinstance(coverage, (int, float)) or not math.isfinite(coverage):
        raise ValidationRuntimeError("alpha_beta.coverage must be a finite number")
    if not isinstance(output.get("warnings"), list) or not all(
        isinstance(item, str) for item in output["warnings"]
    ):
        raise ValidationRuntimeError("alpha_beta.warnings must be a list of strings")
    for name in ("capm", "multi_factor"):
        regression = output.get(name)
        if not isinstance(regression, dict):
            raise ValidationRuntimeError(f"alpha_beta.{name} must be a dictionary")
        if not isinstance(regression.get("betas"), dict) or not isinstance(
            regression.get("estimates"), dict
        ):
            raise ValidationRuntimeError(
                f"alpha_beta.{name} must contain betas and estimates dictionaries"
            )


__all__ = ["ValidationRuntimeError", "execute_validation"]
