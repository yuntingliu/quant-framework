"""Small public facade for data and three-stage Python pipeline research."""

__version__ = "0.5.0"
__author__ = "LYT"
__name_cn__ = "AlphaLab Barebone"

from alphalab.analytics import evaluate_factor
from alphalab.dataio import (
    DataEngine,
    RQDataConfig,
    RQDataProvider,
    create_default_engine,
    create_rq_engine_from_env,
    create_runtime_engine,
)
from alphalab.factors.registry import compute_factor, get_factor, list_factors
from alphalab.pipeline import (
    ComponentRef,
    PipelineProject,
    PipelineRepository,
    STAGE_ENTRYPOINTS,
    STAGE_NAMES,
    preview_pipeline_project,
    run_pipeline_project_backtest,
)
from alphalab.store import ResultStore
from alphalab.strategy.python_runtime import PythonStrategyError, validate_python_source

__all__ = [
    "ComponentRef",
    "DataEngine",
    "PipelineProject",
    "PipelineRepository",
    "PythonStrategyError",
    "RQDataConfig",
    "RQDataProvider",
    "ResultStore",
    "STAGE_ENTRYPOINTS",
    "STAGE_NAMES",
    "compute_factor",
    "create_default_engine",
    "create_rq_engine_from_env",
    "create_runtime_engine",
    "evaluate_factor",
    "get_factor",
    "list_factors",
    "preview_pipeline_project",
    "run_pipeline_project_backtest",
    "validate_python_source",
]
