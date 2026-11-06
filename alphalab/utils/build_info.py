"""Build metadata for local development and immutable server releases."""
from __future__ import annotations

import json
import os
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from alphalab.utils.paths import REPO_ROOT

_RELEASE_FILE = REPO_ROOT / ".alphalab-release.json"
# Kept in step with alphalab.release.MANIFEST_NAME and CORE_LOCK; importing
# that module here would load it on every health probe.
_MANIFEST_FILE = REPO_ROOT / "release-manifest.json"
_CORE_LOCK = REPO_ROOT / "integrations" / "conexus" / "runtime.lock.json"


def build_info() -> dict[str, Any]:
    release = _release_metadata()
    return {
        "version": _package_version(),
        "commit_sha": os.getenv("ALPHALAB_COMMIT_SHA") or release.get("commit_sha") or _git_hash(),
        "deployed_at": os.getenv("ALPHALAB_DEPLOYED_AT") or release.get("deployed_at"),
        "core_revision": _core_revision(),
        "manifest": _manifest_summary(),
    }


def _core_revision() -> str | None:
    lock = _read_json(_CORE_LOCK)
    return lock.get("revision") if lock else None


def _manifest_summary() -> dict[str, Any] | None:
    manifest = _read_json(_MANIFEST_FILE)
    if not manifest:
        return None
    return {
        "schema": manifest.get("schema"),
        "commit_sha": manifest.get("commit_sha"),
        "ref": manifest.get("ref"),
        "created_at": manifest.get("created_at"),
    }


def _package_version() -> str:
    try:
        return version("alphalab")
    except PackageNotFoundError:
        return "0.0.0"


def _release_metadata() -> dict[str, Any]:
    return _read_json(_RELEASE_FILE) or {}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _git_hash() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


__all__ = ["build_info"]
