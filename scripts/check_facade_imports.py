"""Small facade import sanity check for the barebone repo."""

from __future__ import annotations

import importlib
import sys

EXPECTED = [
    "DataEngine",
    "StrategyRepository",
    "StrategyBacktestResult",
    "StrategySourceError",
    "create_default_engine",
    "create_runtime_engine",
    "create_rq_engine_from_env",
    "RQDataConfig",
    "RQDataProvider",
    "inspect_strategy_source",
    "preview_strategy",
    "run_strategy_backtest",
    "evaluate_factor_snapshot",
    "evaluate_factor_history",
    "ResultStore",
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
