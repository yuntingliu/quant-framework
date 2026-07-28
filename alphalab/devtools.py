"""Local development diagnostics without exposing credential values."""
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
    load_env_files()
    checks = {
        "python": _python_check(),
        "node": _node_check(),
        "demo_data": _demo_data_check(),
        "runtime": DataCatalog().summary(),
        "rq": _rq_check(),
        "frontend": _frontend_check(),
        "ports": {
            "8000": _port_status(8000),
            "5173": _port_status(5173),
        },
    }
    required = ("python", "node", "demo_data", "frontend")
    status = (
        "ready"
        if all(checks[name].get("status") == "ready" for name in required)
        else "degraded"
    )
    return {"status": status, "checks": checks}


def _python_check() -> dict[str, Any]:
    supported = sys.version_info >= (3, 10)
    return {
        "status": "ready" if supported else "unsupported",
        "version": platform.python_version(),
        "executable": sys.executable,
    }


def _node_check() -> dict[str, Any]:
    executable = shutil.which("node")
    if executable is None:
        return {"status": "missing", "version": None, "executable": None}
    try:
        version = subprocess.check_output(
            [executable, "--version"],
            text=True,
            timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return {"status": "invalid", "version": None, "executable": executable}
    return {"status": "ready", "version": version, "executable": executable}


def _demo_data_check() -> dict[str, Any]:
    manifest_path = DATA_DIR / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "invalid", "error": str(exc), "files": 0}

    failures: list[str] = []
    files = manifest.get("files", {})
    for relative, metadata in files.items():
        path = REPO_ROOT / relative
        if not path.is_file():
            failures.append(f"missing:{relative}")
            continue
        expected = metadata.get("sha256") or metadata.get("seed_sha256")
        if expected and _sha256(path) != expected:
            failures.append(f"checksum:{relative}")
    return {
        "status": "ready" if not failures else "invalid",
        "files": len(files),
        "symbol_count": manifest.get("symbol_count"),
        "cutoff_date": manifest.get("cutoff_date"),
        "issues": failures,
    }


def _rq_check() -> dict[str, Any]:
    present = {key: bool(os.getenv(key, "").strip()) for key in _RQ_KEYS}
    missing = [key for key, configured in present.items() if not configured]
    return {
        "status": "configured" if not missing else "not_configured",
        "configured": not missing,
        "missing": missing,
    }


def _frontend_check() -> dict[str, Any]:
    frontend = REPO_ROOT / "dashboard" / "frontend"
    source_ready = (frontend / "package.json").is_file() and (frontend / "src").is_dir()
    return {
        "status": "ready" if source_ready else "missing",
        "source": "ready" if source_ready else "missing",
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
