"""Load local project environment files without overriding shell variables."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from alphalab.utils.paths import REPO_ROOT


def env_file_candidates() -> list[Path]:
    """Return the supported project-local environment files in priority order."""

    candidates: list[Path] = []
    explicit = os.getenv("ALPHALAB_ENV_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend((REPO_ROOT / ".env", REPO_ROOT / ".env.local"))
    return list(dict.fromkeys(candidates))


def load_env_files(candidates: Iterable[Path] | None = None) -> list[Path]:
    """Load existing env files and return those read successfully.

    Existing process variables always win, so CI and shell configuration remain
    authoritative over repository-local files.
    """

    loaded: list[Path] = []
    for path in candidates if candidates is not None else env_file_candidates():
        if path.is_file() and _load_env_file(path):
            loaded.append(path)
    return loaded


def _load_env_file(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return False

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, raw_value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
    return True


__all__ = ["env_file_candidates", "load_env_files"]
