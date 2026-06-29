"""Bundled generic strategy templates."""
from __future__ import annotations

from pathlib import Path

STRATEGIES_DIR = Path(__file__).resolve().parent


def list_strategy_files() -> list[Path]:
    return sorted(path for path in STRATEGIES_DIR.glob("*.yaml") if path.is_file())


__all__ = ["STRATEGIES_DIR", "list_strategy_files"]

