"""Build metadata for local development and immutable server releases."""
from __future__ import annotations

import json
import os
import subprocess
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from alphalab.utils.paths import REPO_ROOT

_RELEASE_FILE = REPO_ROOT / ".alphalab-release.json"


def build_info() -> dict[str, Any]:
    release = _release_metadata()
    return {
        "version": _package_version(),
        "commit_sha": os.getenv("ALPHALAB_COMMIT_SHA") or release.get("commit_sha") or _git_hash(),
        "deployed_at": os.getenv("ALPHALAB_DEPLOYED_AT") or release.get("deployed_at"),
    }


def _package_version() -> str:
    try:
        return version("alphalab")
    except PackageNotFoundError:
        return "0.0.0"


def _release_metadata() -> dict[str, Any]:
    try:
        value = json.loads(_RELEASE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


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
