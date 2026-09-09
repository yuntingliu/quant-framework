"""Repository-local paths.

Local data and generated artifacts are deliberately outside git. The default
paths are useful for development, while tests and applications can pass explicit
paths into the relevant constructors.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parent

DATA_DIR = REPO_ROOT / "data"
APP_DATA_DIR = Path(os.environ.get("ALPHALAB_APP_DATA_DIR", DATA_DIR / "app")).expanduser()
CACHE_DIR = Path(os.environ.get("ALPHALAB_CACHE_DIR", DATA_DIR / "cache")).expanduser()
MARKET_DIR = DATA_DIR / "market"
FUNDAMENTAL_DIR = DATA_DIR / "fundamentals"
FACTOR_DIR = DATA_DIR / "factors"
RUNTIME_DIR = Path(os.environ.get("ALPHALAB_RUNTIME_DIR", DATA_DIR / "runtime")).expanduser()
RUNTIME_APP_DIR = RUNTIME_DIR / "app"


def ensure_local_dirs() -> None:
    """Create local runtime directories when the framework writes state."""

    for path in (
        APP_DATA_DIR,
        CACHE_DIR,
        MARKET_DIR,
        FUNDAMENTAL_DIR,
        FACTOR_DIR,
        RUNTIME_DIR,
        RUNTIME_APP_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
