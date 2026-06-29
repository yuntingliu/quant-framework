"""AlphaLab barebone quant framework.

The package keeps the stable research loop small:

    DataEngine -> StrategyConfig -> SignalEngine -> run_backtest

Concrete market data vendors and broker adapters are intentionally outside this
barebone repository. Add them later by implementing the provider protocols in
``alphalab.dataio.providers.protocol`` or the broker protocol in
``alphalab.execution.broker``.
"""

__version__ = "0.3.0"
__author__ = "LYT"
__name_cn__ = "AlphaLab Barebone"

# -- Public facade ------------------------------------------------------------
from alphalab.dataio import DataEngine, create_default_engine
from alphalab.engine import SignalEngine, run_backtest
from alphalab.factors.registry import compute_factor, get_factor, list_factors
from alphalab.store import ResultStore
from alphalab.strategy.config import FactorSpec, StrategyConfig, UniverseSpec

__all__ = [
    "DataEngine",
    "create_default_engine",
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
