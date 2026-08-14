"""AlphaLab barebone quant framework.

The package keeps the stable research loops small:

    DataEngine -> StrategyConfig -> SignalEngine -> run_backtest
    DataEngine -> TimingStrategyConfig -> run_timing_backtest

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
from alphalab.strategy.timing import (
    TIMING_SIGNAL_KINDS,
    TimingExecutionSpec,
    TimingPositionSpec,
    TimingSignalSpec,
    TimingStrategyConfig,
)
from alphalab.strategy.implementation import StrategyImplementationSpec
from alphalab.strategy.python_runtime import PythonStrategyError, validate_python_source
from alphalab.timing import TimingBacktestResult, evaluate_timing_signals, run_timing_backtest

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
    "TIMING_SIGNAL_KINDS",
    "TimingExecutionSpec",
    "TimingPositionSpec",
    "TimingSignalSpec",
    "TimingStrategyConfig",
    "StrategyImplementationSpec",
    "PythonStrategyError",
    "validate_python_source",
    "TimingBacktestResult",
    "evaluate_timing_signals",
    "run_timing_backtest",
    "list_factors",
    "compute_factor",
    "get_factor",
    "evaluate_factor",
]
