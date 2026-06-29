"""Repository-local paths.

Local data and generated artifacts are deliberately outside git. The default
paths are useful for development, while tests and applications can pass explicit
paths into the relevant constructors.
"""
from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parent

DATA_DIR = REPO_ROOT / "data"
APP_DATA_DIR = DATA_DIR / "app"
CACHE_DIR = DATA_DIR / "cache"
MARKET_DIR = DATA_DIR / "market"
FUNDAMENTAL_DIR = DATA_DIR / "fundamentals"
FACTOR_DIR = DATA_DIR / "factors"


def ensure_local_dirs() -> None:
    """Create local runtime directories when the framework writes state."""

    for path in (APP_DATA_DIR, CACHE_DIR, MARKET_DIR, FUNDAMENTAL_DIR, FACTOR_DIR):
        path.mkdir(parents=True, exist_ok=True)

