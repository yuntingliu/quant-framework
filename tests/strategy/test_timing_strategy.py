from __future__ import annotations

import pandas as pd

from alphalab import (
    TimingStrategyConfig,
    create_default_engine,
    evaluate_timing_signals,
    run_timing_backtest,
)
from alphalab.strategy import TimingStrategyRepository


def test_timing_config_and_repository_are_explicitly_typed(tmp_path) -> None:
    repository = TimingStrategyRepository(tmp_path / "timing_strategies")
    definitions = repository.list()

    assert {item.id for item in definitions} == {"timing_defensive", "timing_trend"}
    assert all(item.as_dict()["strategy_type"] == "market_timing" for item in definitions)
    config = definitions[0].config
    assert TimingStrategyConfig.from_dict(config.to_dict()) == config
    assert config.to_dict()["strategy_type"] == "market_timing"
    clone = repository.clone("timing_trend", "local_timing")
    assert clone.built_in is False
    assert clone.path.parent == tmp_path / "timing_strategies"
    assert repository.delete("local_timing") is True


def test_timing_exposure_combines_registered_signal_scores() -> None:
    config = TimingStrategyConfig.from_dict(
        {
            "strategy_type": "market_timing",
            "name": "timing_test",
            "signals": [
                {"kind": "momentum", "weight": 1.0, "window": 2, "threshold": 0.0},
            ],
            "position": {"min_exposure": 0.2, "max_exposure": 0.8},
        }
    )
    market = pd.Series(
        [0.10, 0.10, -0.30, -0.10],
        index=pd.date_range("2024-01-31", periods=4, freq="M"),
    )

    scores = evaluate_timing_signals(config, market)

    assert scores["exposure"].between(0.2, 0.8).all()
    assert scores["exposure"].iloc[1] == 0.8
    assert scores["exposure"].iloc[-1] == 0.2


def test_timing_backtest_uses_lagged_monthly_exposure() -> None:
    config = TimingStrategyRepository().get("timing_trend").config  # type: ignore[union-attr]
    result = run_timing_backtest(
        config,
        "2023-01-01",
        "2025-12-31",
        create_default_engine(),
    )

    assert len(result.returns) == len(result.benchmark) == len(result.exposure)
    assert result.exposure.between(0.0, 1.0).all()
    assert result.diagnostics["strategy_type"] == "market_timing"
    assert result.executions[0]["signal_date"] is None
    assert result.executions[0]["entry_date"] == str(result.returns.index[0])[:10]
