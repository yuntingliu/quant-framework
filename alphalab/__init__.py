"""AlphaLab barebone quant framework.

The package keeps the stable research loop small:

    DataEngine -> StrategyConfig -> SignalEngine -> run_backtest

Concrete broker adapters remain outside this barebone repository. RQData is an
optional information provider configured from local environment values.
"""

__version__ = "0.3.0"
__author__ = "LYT"
__name_cn__ = "AlphaLab Barebone"

# -- Public facade ------------------------------------------------------------
from alphalab.dataio import (
    DataEngine,
    RQDataConfig,
    RQDataProvider,
    create_default_engine,
    create_rq_engine_from_env,
)
from alphalab.engine import SignalEngine, run_backtest
from alphalab.factors.registry import compute_factor, get_factor, list_factors
from alphalab.store import ResultStore
from alphalab.strategy.config import FactorSpec, StrategyConfig, UniverseSpec

__all__ = [
    "DataEngine",
    "create_default_engine",
    "create_rq_engine_from_env",
    "RQDataConfig",
    "RQDataProvider",
    "SignalEngine",
    "run_backtest",
    "ResultStore",
    "StrategyConfig",
    "FactorSpec",
    "UniverseSpec",
    "list_factors",
    "compute_factor",
    "get_factor",
]
