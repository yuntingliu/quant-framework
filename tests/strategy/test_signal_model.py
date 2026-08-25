from __future__ import annotations

import pandas as pd

from alphalab.engine import SignalEngine
from alphalab.pipeline.builtins import SELECTION_FACTOR_TOP
from alphalab.strategy import StrategyConfig
from alphalab.strategy.python_runtime import execute_python_strategy


def test_legacy_execution_frequency_migrates_to_signal_model() -> None:
    config = StrategyConfig.from_dict(
        {
            "name": "legacy",
            "selection": {"n_stocks": 5},
            "execution": {"rebalance_freq": "weekly", "cost_bps": 10.0},
        }
    )

    assert config.selection.signal_frequency == "weekly"
    assert "rebalance_freq" not in config.to_dict()["execution"]
    assert config.to_dict()["selection"]["signal_frequency"] == "weekly"


def test_signal_model_weights_override_legacy_factor_weights() -> None:
    config = StrategyConfig.from_dict(
        {
            "name": "weighted",
            "factors": [
                {"name": "momentum_20d", "source": "technical", "weight": 9.0},
                {"name": "volatility_20d", "source": "technical", "weight": 7.0},
            ],
            "selection": {
                "factor_weights": {"momentum_20d": 1.0, "volatility_20d": 0.0}
            },
        }
    )

    assert config.total_weight == 1.0
    assert config.active_factor_names == ["momentum_20d"]


def test_all_scope_resolves_initial_candidates_from_instrument_master() -> None:
    class DataStub:
        def get_instruments(self, asof_date=None):
            assert asof_date == "2025-01-31"
            return pd.DataFrame({"symbol": ["BBB", "AAA", "BBB"]})

        def get_symbols(self, universe="all"):
            return ["AAA"]

    config = StrategyConfig(name="all-a-shares")

    symbols = SignalEngine(DataStub())._resolve_symbols(config, "2025-01-31")

    assert symbols == ["AAA", "BBB"]


def test_rank_buffer_keeps_an_incumbent_until_exit_rank() -> None:
    candidates = [
        {"symbol": "AAA", "factor_scores": {"value": 4.0}},
        {"symbol": "BBB", "factor_scores": {"value": 3.0}},
        {"symbol": "CCC", "factor_scores": {"value": 2.0}},
        {"symbol": "DDD", "factor_scores": {"value": 1.0}},
    ]
    execution = execute_python_strategy(
        SELECTION_FACTOR_TOP,
        "select_assets",
        [
            {
                "parameters": {
                    "count": 2,
                    "exit_rank": 3,
                    "min_factor_coverage": 1.0,
                },
                "factor_names": ["value"],
                "candidates": candidates,
                "current_weights": {"CCC": 0.5},
            }
        ],
        5.0,
    )

    assert execution.values[0]["selected"] == ["AAA", "CCC"]
