"""Local development diagnostics that never expose credential values."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from alphalab.dataio.catalog import DataCatalog
from alphalab.utils.env import load_env_files
from alphalab.utils.paths import DATA_DIR, REPO_ROOT

_RQ_KEYS = ("RQ_USER", "RQ_PASSWORD", "RQ_HOST")


def doctor_report() -> dict[str, Any]:
    """Return bounded readiness checks for the one supported local runtime."""

    load_env_files()
    runtime = DataCatalog().summary()
    checks = {
        "python": _executable_check(sys.executable, ["--version"], minimum=(3, 10)),
        "node": _command_check("node", ["--version"]),
        "demo_data": _demo_data_check(),
        "rq": _rq_check(),
        "runtime": runtime,
        "runtime_execution": _runtime_execution_check(runtime),
        "frontend": _frontend_check(),
        "pyrefly": _command_check("pyrefly", ["--version"]),
        "ruff": _module_check("ruff"),
        "ports": {
            "8000": _port_status(8000),
            "5173": _port_status(5173),
        },
    }
    required = ("python", "node", "demo_data", "frontend", "runtime_execution")
    status = (
        "ready"
        if all(checks[name].get("status") == "ready" for name in required)
        else "degraded"
    )
    return {"status": status, "checks": checks}


def _runtime_execution_check(runtime: dict[str, Any]) -> dict[str, Any]:
    required = ("rq.instruments", "rq.bars", "rq.paused")
    states = {
        str(item.get("id")): str(item.get("status"))
        for item in runtime.get("datasets", [])
    }
    missing = [dataset for dataset in required if states.get(dataset) != "ready"]
    return {
        "status": "ready" if not missing else "missing",
        "required_datasets": list(required),
        "missing": missing,
    }


def _executable_check(
    executable: str,
    arguments: list[str],
    *,
    minimum: tuple[int, int] | None = None,
) -> dict[str, Any]:
    result = _run_version(executable, arguments)
    if minimum and sys.version_info < minimum:
        result["status"] = "unsupported"
    result["executable"] = executable
    return result


def _command_check(command: str, arguments: list[str]) -> dict[str, Any]:
    executable = shutil.which(command)
    if executable is None:
        return {"status": "missing", "version": None, "executable": None}
    result = _run_version(executable, arguments)
    result["executable"] = executable
    return result


def _module_check(module: str) -> dict[str, Any]:
    result = _run_version(sys.executable, ["-m", module, "--version"])
    result["executable"] = sys.executable
    return result


def _run_version(executable: str, arguments: list[str]) -> dict[str, Any]:
    try:
        value = subprocess.check_output(
            [executable, *arguments],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return {"status": "invalid", "version": None}
    return {"status": "ready", "version": value or platform.python_version()}


def _rq_check() -> dict[str, Any]:
    present = {key: bool(os.getenv(key, "").strip()) for key in _RQ_KEYS}
    missing = [key for key, configured in present.items() if not configured]
    return {
        "status": "configured" if not missing else "not_configured",
        "configured": not missing,
        "missing": missing,
    }


def _demo_data_check() -> dict[str, Any]:
    manifest_path = DATA_DIR / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "invalid", "error": str(exc), "files": 0}
    failures: list[str] = []
    files = manifest.get("files", {})
    for relative, metadata in files.items():
        path = Path(REPO_ROOT) / relative
        if not path.is_file():
            failures.append(f"missing:{relative}")
            continue
        # Seeded SQLite state is intentionally mutable after first launch.  Only
        # immutable bundled artifacts use ``sha256`` as an ongoing integrity gate.
        expected = metadata.get("sha256")
        if expected and _sha256(path) != expected:
            failures.append(f"checksum:{relative}")
    return {
        "status": "ready" if not failures else "invalid",
        "files": len(files),
        "symbol_count": manifest.get("symbol_count"),
        "cutoff_date": manifest.get("cutoff_date"),
        "issues": failures,
    }


def _frontend_check() -> dict[str, Any]:
    frontend = Path(REPO_ROOT) / "dashboard" / "frontend"
    source_ready = (frontend / "package.json").is_file() and (frontend / "src").is_dir()
    return {
        "status": "ready" if source_ready else "missing",
        "source": "ready" if source_ready else "missing",
        "dependencies": "ready" if (frontend / "node_modules").is_dir() else "missing",
        "build": "ready" if (frontend / "dist" / "index.html").is_file() else "missing",
    }


def _port_status(port: int) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.2)
        return "listening" if connection.connect_ex(("127.0.0.1", port)) == 0 else "available"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["doctor_report"]
