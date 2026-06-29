"""Small facade import sanity check for the barebone repo."""
from __future__ import annotations

import importlib
import sys


EXPECTED = [
    "DataEngine",
    "create_default_engine",
    "StrategyConfig",
    "SignalEngine",
    "run_backtest",
    "ResultStore",
    "list_factors",
    "compute_factor",
    "get_factor",
]


def main() -> int:
    module = importlib.import_module("alphalab")
    missing = [name for name in EXPECTED if not hasattr(module, name)]
    if missing:
        print(f"Missing facade exports: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
