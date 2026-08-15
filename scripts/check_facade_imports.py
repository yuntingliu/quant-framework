"""Small facade import sanity check for the barebone repo."""
from __future__ import annotations

import importlib
import sys

EXPECTED = [
    "ComponentRef",
    "DataEngine",
    "PipelineProject",
    "PipelineRepository",
    "STAGE_ENTRYPOINTS",
    "STAGE_NAMES",
    "create_default_engine",
    "create_runtime_engine",
    "create_rq_engine_from_env",
    "RQDataConfig",
    "RQDataProvider",
    "preview_pipeline_project",
    "run_pipeline_project_backtest",
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
