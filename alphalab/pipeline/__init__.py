"""Six-stage, Python-native strategy composition facade."""
from alphalab.pipeline.compose import ComposedStrategy, compose_strategy
from alphalab.pipeline.models import (
    ComponentRef,
    PipelineProject,
    STAGE_ENTRYPOINTS,
    STAGE_NAMES,
)
from alphalab.pipeline.repository import PipelineRepository
from alphalab.pipeline.runtime import (
    PipelineBacktestResult,
    preview_pipeline_project,
    project_strategy_config,
    run_pipeline_project_backtest,
)

__all__ = [
    "ComponentRef",
    "ComposedStrategy",
    "PipelineProject",
    "PipelineBacktestResult",
    "PipelineRepository",
    "STAGE_ENTRYPOINTS",
    "STAGE_NAMES",
    "compose_strategy",
    "preview_pipeline_project",
    "project_strategy_config",
    "run_pipeline_project_backtest",
]
