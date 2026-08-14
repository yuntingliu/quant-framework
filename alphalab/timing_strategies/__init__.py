"""Bundled market-timing strategy templates."""
from __future__ import annotations

from pathlib import Path

TIMING_STRATEGIES_DIR = Path(__file__).resolve().parent


def list_timing_strategy_files() -> list[Path]:
    return sorted(path for path in TIMING_STRATEGIES_DIR.glob("*.yaml") if path.is_file())


__all__ = ["TIMING_STRATEGIES_DIR", "list_timing_strategy_files"]
