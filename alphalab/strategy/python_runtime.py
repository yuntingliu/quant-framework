"""Validation and isolated execution for trusted local Python strategies."""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_PYTHON_SOURCE_BYTES = 100_000


class PythonStrategyError(ValueError):
    """Raised when a custom Python strategy cannot be validated or executed."""


@dataclass(frozen=True)
class PythonStrategyExecution:
    values: list[Any]
    stdout: str
    stderr: str
    duration_seconds: float
    source_sha256: str


def validate_python_source(source: str, entrypoint: str = "generate") -> dict:
    if not entrypoint.isidentifier() or entrypoint.startswith("_"):
        raise PythonStrategyError("entrypoint must be a public Python identifier")
    encoded = source.encode("utf-8")
    if not source.strip():
        raise PythonStrategyError("python_source must not be empty")
    if len(encoded) > MAX_PYTHON_SOURCE_BYTES:
        raise PythonStrategyError(
            f"python_source must not exceed {MAX_PYTHON_SOURCE_BYTES} bytes"
        )
    try:
        tree = ast.parse(source, filename="<alphalab-custom-strategy>")
    except SyntaxError as exc:
        raise PythonStrategyError(
            f"Python syntax error at line {exc.lineno}: {exc.msg}"
        ) from exc
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    if entrypoint not in definitions:
        raise PythonStrategyError(
            f"python_source must define def {entrypoint}(context)"
        )
    return {
        "valid": True,
        "entrypoint": entrypoint,
        "bytes": len(encoded),
        "sha256": python_source_sha256(source),
        "trusted_local_code": True,
    }


def execute_python_strategy(
    source: str,
    entrypoint: str,
    contexts: list[dict],
    timeout_seconds: float,
) -> PythonStrategyExecution:
    """Execute a hook in a child process and return its JSON-compatible values.

    Process isolation supplies crash and timeout containment. It is deliberately
    described as trusted-local execution, not as a security sandbox.
    """

    validation = validate_python_source(source, entrypoint)
    request = json.dumps(
        {"source": source, "entrypoint": entrypoint, "contexts": contexts},
        allow_nan=False,
    )
    worker = Path(__file__).with_name("python_worker.py")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(worker)],
            input=request,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
            check=False,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired as exc:
        raise PythonStrategyError(
            f"Python strategy exceeded the {timeout_seconds:g}s timeout"
        ) from exc
    duration = time.perf_counter() - started
    if completed.returncode != 0:
        raise PythonStrategyError(
            f"Python strategy worker exited with code {completed.returncode}: "
            f"{completed.stderr[-1000:]}"
        )
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PythonStrategyError("Python strategy worker returned invalid output") from exc
    if not response.get("ok"):
        detail = response.get("error") or "unknown Python strategy error"
        trace = response.get("traceback")
        suffix = f"\n{trace}" if trace else ""
        raise PythonStrategyError(f"{detail}{suffix}")
    values = response.get("values")
    if not isinstance(values, list) or len(values) != len(contexts):
        raise PythonStrategyError("Python strategy returned an invalid result batch")
    return PythonStrategyExecution(
        values=values,
        stdout=str(response.get("stdout") or ""),
        stderr=str(response.get("stderr") or ""),
        duration_seconds=duration,
        source_sha256=str(validation["sha256"]),
    )


def python_source_sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


__all__ = [
    "MAX_PYTHON_SOURCE_BYTES",
    "PythonStrategyError",
    "PythonStrategyExecution",
    "execute_python_strategy",
    "python_source_sha256",
    "validate_python_source",
]
