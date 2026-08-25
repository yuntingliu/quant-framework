"""Isolated execution boundary for the non-authoritative Python Lab.

Docker is the production-safe option: the container has no network, no host
mounts, a read-only root filesystem, no Linux capabilities, and bounded
resources.  ``trusted_local`` exists for controlled development machines only;
it is intentionally described as unsafe and requires a separate confirmation.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4


_RUNTIME_KINDS = {"disabled", "docker", "trusted_local"}
_MAX_SOURCE_BYTES = 100_000
_MAX_CONTEXT_BYTES = 16_000_000
_MAX_RESULT_BYTES = 2_000_000
_MAX_CAPTURE_BYTES = 20_000


def _positive_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _positive_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class PythonLabConfig:
    kind: str = "disabled"
    docker_image: str = ""
    timeout_seconds: int = 20
    memory_mb: int = 512
    cpus: float = 1.0
    pids_limit: int = 64

    def __post_init__(self) -> None:
        if self.kind not in _RUNTIME_KINDS:
            raise ValueError(f"python lab runtime must be one of {sorted(_RUNTIME_KINDS)}")
        if self.kind == "docker" and not self.docker_image.strip():
            raise ValueError("ALPHALAB_PYTHON_LAB_IMAGE is required for docker runtime")

    @classmethod
    def from_env(cls) -> "PythonLabConfig":
        return cls(
            kind=os.getenv("ALPHALAB_PYTHON_LAB_RUNTIME", "disabled").strip().lower(),
            docker_image=os.getenv("ALPHALAB_PYTHON_LAB_IMAGE", "").strip(),
            timeout_seconds=_positive_int(
                "ALPHALAB_PYTHON_LAB_TIMEOUT_SECONDS", 20, minimum=1, maximum=120
            ),
            memory_mb=_positive_int(
                "ALPHALAB_PYTHON_LAB_MEMORY_MB", 512, minimum=128, maximum=4096
            ),
            cpus=_positive_float("ALPHALAB_PYTHON_LAB_CPUS", 1.0, minimum=0.1, maximum=4.0),
            pids_limit=_positive_int("ALPHALAB_PYTHON_LAB_PIDS_LIMIT", 64, minimum=16, maximum=512),
        )

    def capabilities(self) -> dict[str, Any]:
        enabled = self.kind != "disabled"
        isolated = self.kind == "docker"
        available = enabled
        reason: str | None = None
        if self.kind == "disabled":
            available = False
            reason = "Python Lab is disabled by configuration."
        elif self.kind == "docker" and shutil.which("docker") is None:
            available = False
            reason = "Docker CLI is not available."
        elif self.kind == "docker":
            docker = shutil.which("docker")
            assert docker is not None
            try:
                inspected = subprocess.run(
                    [docker, "image", "inspect", self.docker_image],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                available = False
                reason = "Docker engine is unavailable."
            else:
                if inspected.returncode != 0:
                    available = False
                    reason = (
                        "Configured Python Lab image is not present locally; "
                        "automatic pulls are disabled."
                    )
        return {
            "enabled": enabled,
            "available": available,
            "runtime_kind": self.kind,
            "isolated": isolated,
            "trusted_local": self.kind == "trusted_local",
            "authoritative": False,
            "network_access": False if isolated else None,
            "host_mounts": False if isolated else None,
            "read_only_root": True if isolated else None,
            "limits": {
                "timeout_seconds": self.timeout_seconds,
                "memory_mb": self.memory_mb if isolated else None,
                "cpus": self.cpus if isolated else None,
                "pids": self.pids_limit if isolated else None,
                "source_bytes": _MAX_SOURCE_BYTES,
                "context_bytes": _MAX_CONTEXT_BYTES,
                "result_bytes": _MAX_RESULT_BYTES,
            },
            "reason": reason,
        }


@dataclass(frozen=True)
class PythonLabExecution:
    output: Any
    stdout: str
    stderr: str


def validate_lab_source(source: str) -> dict[str, Any]:
    encoded = source.encode("utf-8")
    if not encoded:
        raise ValueError("Python Lab source must not be empty")
    if len(encoded) > _MAX_SOURCE_BYTES:
        raise ValueError(f"Python Lab source exceeds {_MAX_SOURCE_BYTES} bytes")
    try:
        tree = ast.parse(source, filename="python_lab.py", mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"invalid Python source: {exc.msg} at line {exc.lineno}") from exc
    entrypoints = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run"
    ]
    if len(entrypoints) != 1 or isinstance(entrypoints[0], ast.AsyncFunctionDef):
        raise ValueError(
            "Python Lab source must define exactly one synchronous run(context) function"
        )
    function = entrypoints[0]
    positional = [*function.args.posonlyargs, *function.args.args]
    if (
        len(positional) != 1
        or function.args.vararg
        or function.args.kwarg
        or function.args.kwonlyargs
        or function.args.defaults
    ):
        raise ValueError("Python Lab entrypoint must have the signature run(context)")
    return {
        "entrypoint": "run",
        "source_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


_WORKER_SOURCE = r"""
import contextlib
import json
import sys
import traceback

class BoundedText:
    def __init__(self, limit):
        self.limit = limit
        self.parts = []
        self.length = 0

    def write(self, value):
        text = str(value)
        remaining = max(0, self.limit - self.length)
        if remaining:
            chunk = text[:remaining]
            self.parts.append(chunk)
            self.length += len(chunk)
        return len(text)

    def flush(self):
        return None

    def getvalue(self):
        return "".join(self.parts)

def main():
    request = json.loads(sys.stdin.read())
    namespace = {"__name__": "__alphalab_python_lab__"}
    stdout = BoundedText(20000)
    stderr = BoundedText(20000)
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compile(request["source"], "python_lab.py", "exec"), namespace, namespace)
            result = namespace["run"](request["context"])
            json.dumps(result, allow_nan=False)
        envelope = {"ok": True, "output": result, "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}
    except BaseException as exc:
        envelope = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue() + traceback.format_exc(),
        }
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False, allow_nan=False))

main()
"""


class PythonLabRuntime:
    def __init__(self, config: PythonLabConfig | None = None):
        self.config = config or PythonLabConfig.from_env()

    def execute(
        self,
        source: str,
        context: Mapping[str, Any],
        *,
        confirm_trusted_local: bool = False,
    ) -> PythonLabExecution:
        validate_lab_source(source)
        context_payload = json.dumps(
            dict(context), ensure_ascii=False, sort_keys=True, allow_nan=False
        ).encode("utf-8")
        if len(context_payload) > _MAX_CONTEXT_BYTES:
            raise ValueError(f"Python Lab context exceeds {_MAX_CONTEXT_BYTES} bytes")
        if self.config.kind == "disabled":
            raise PermissionError("Python Lab is disabled by configuration")
        if self.config.kind == "trusted_local" and not confirm_trusted_local:
            raise PermissionError("trusted_local execution requires confirm_trusted_local=true")

        request = json.dumps(
            {"source": source, "context": dict(context)},
            ensure_ascii=False,
            allow_nan=False,
        )
        command = self._command()
        try:
            completed = subprocess.run(
                command,
                input=request,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.timeout_seconds,
                check=False,
                env=self._environment(),
            )
        except subprocess.TimeoutExpired as exc:
            self._remove_timed_out_container(command)
            raise TimeoutError(
                f"Python Lab exceeded {self.config.timeout_seconds} seconds"
            ) from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip()[:_MAX_CAPTURE_BYTES]
            raise RuntimeError(
                f"Python Lab runtime exited with code {completed.returncode}: {detail}"
            )
        if len(completed.stdout.encode("utf-8")) > _MAX_RESULT_BYTES + 100_000:
            raise RuntimeError("Python Lab worker response exceeded the output limit")
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Python Lab worker returned an invalid response") from exc
        stdout = str(envelope.get("stdout") or "")[:_MAX_CAPTURE_BYTES]
        stderr = str(envelope.get("stderr") or "")[:_MAX_CAPTURE_BYTES]
        if not envelope.get("ok"):
            message = str(envelope.get("error") or "Python Lab execution failed")
            error = RuntimeError(message)
            error.stdout = stdout  # type: ignore[attr-defined]
            error.stderr = stderr  # type: ignore[attr-defined]
            raise error
        output = envelope.get("output")
        output_payload = json.dumps(output, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(output_payload) > _MAX_RESULT_BYTES:
            raise RuntimeError(f"Python Lab result exceeds {_MAX_RESULT_BYTES} bytes")
        return PythonLabExecution(output=output, stdout=stdout, stderr=stderr)

    def _command(self) -> list[str]:
        if self.config.kind == "trusted_local":
            return [sys.executable, "-I", "-c", _WORKER_SOURCE]
        if self.config.kind != "docker":
            raise PermissionError("Python Lab is disabled by configuration")
        docker = shutil.which("docker")
        if docker is None:
            raise RuntimeError("Docker CLI is not available")
        inspected = subprocess.run(
            [docker, "image", "inspect", self.config.docker_image],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if inspected.returncode != 0:
            raise RuntimeError(
                "Configured Python Lab image is not present locally; automatic pulls are disabled"
            )
        return [
            docker,
            "run",
            "--rm",
            "-i",
            f"--name=alphalab-python-lab-{uuid4().hex}",
            "--stop-timeout=1",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--user=65534:65534",
            "--entrypoint=python",
            "--ulimit=nofile=256:256",
            f"--pids-limit={self.config.pids_limit}",
            f"--memory={self.config.memory_mb}m",
            f"--cpus={self.config.cpus}",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
            self.config.docker_image,
            "-I",
            "-c",
            _WORKER_SOURCE,
        ]

    def _remove_timed_out_container(self, command: list[str]) -> None:
        if self.config.kind != "docker" or not command:
            return
        name = next(
            (item.removeprefix("--name=") for item in command if item.startswith("--name=")),
            "",
        )
        if not name:
            return
        try:
            subprocess.run(
                [command[0], "rm", "-f", name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            # The original timeout remains the actionable error. Operators can
            # inspect Docker separately if its control plane is unavailable.
            return

    def _environment(self) -> dict[str, str] | None:
        if self.config.kind == "docker":
            return None
        # Isolated Python ignores PYTHON* variables.  Pass only the minimum
        # process environment needed to start the configured interpreter.
        names = ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP")
        return {name: os.environ[name] for name in names if name in os.environ}


__all__ = [
    "PythonLabConfig",
    "PythonLabExecution",
    "PythonLabRuntime",
    "validate_lab_source",
]
