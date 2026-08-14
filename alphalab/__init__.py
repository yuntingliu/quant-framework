"""AlphaLab barebone quant framework.

The package keeps the stable research loop small:

    DataEngine -> StrategyConfig -> SignalEngine -> run_backtest

Concrete broker adapters remain outside this barebone repository. RQData is an
optional information provider configured from local environment values.
"""

__version__ = "0.4.0"
__author__ = "LYT"
__name_cn__ = "AlphaLab Barebone"

# -- Public facade ------------------------------------------------------------
from alphalab.analytics import evaluate_factor
from alphalab.dataio import (
    DataEngine,
    RQDataConfig,
    RQDataProvider,
    create_default_engine,
    create_rq_engine_from_env,
    create_runtime_engine,
)
from alphalab.engine import BacktestResult, SignalEngine, run_backtest, run_backtest_detailed
from alphalab.factors.registry import compute_factor, get_factor, list_factors
from alphalab.store import ResultStore
from alphalab.strategy.config import (
    ExecutionSpec,
    FactorSpec,
    PortfolioSpec,
    SelectionSpec,
    StrategyConfig,
    UniverseSpec,
)

__all__ = [
    "DataEngine",
    "create_default_engine",
    "create_runtime_engine",
    "create_rq_engine_from_env",
    "RQDataConfig",
    "RQDataProvider",
    "SignalEngine",
    "BacktestResult",
    "run_backtest",
    "run_backtest_detailed",
    "ResultStore",
    "StrategyConfig",
    "FactorSpec",
    "SelectionSpec",
    "PortfolioSpec",
    "ExecutionSpec",
    "UniverseSpec",
    "list_factors",
    "compute_factor",
    "get_factor",
    "evaluate_factor",
]
