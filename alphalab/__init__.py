"""Small public facade for data and Strategy SDK v1 research."""

__version__ = "0.5.0"
__author__ = "LYT"
__name_cn__ = "AlphaLab Barebone"

from alphalab.dataio import (
    DataEngine,
    RQDataConfig,
    RQDataProvider,
    create_default_engine,
    create_rq_engine_from_env,
    create_runtime_engine,
)
from alphalab.store import ResultStore
from alphalab.strategy.engine import (
    StrategyBacktestResult,
    evaluate_factor_history,
    evaluate_factor_research,
    evaluate_factor_snapshot,
    preview_strategy,
    run_strategy_backtest,
)
from alphalab.strategy.repository import StrategyRepository
from alphalab.strategy.source import StrategySourceError, inspect_strategy_source

__all__ = [
    "DataEngine",
    "RQDataConfig",
    "RQDataProvider",
    "ResultStore",
    "StrategyBacktestResult",
    "StrategyRepository",
    "StrategySourceError",
    "create_default_engine",
    "create_rq_engine_from_env",
    "create_runtime_engine",
    "evaluate_factor_history",
    "evaluate_factor_research",
    "evaluate_factor_snapshot",
    "inspect_strategy_source",
    "preview_strategy",
    "run_strategy_backtest",
]
