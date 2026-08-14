from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alphalab import SignalEngine, StrategyConfig, create_default_engine
from alphalab.provenance import build_research_provenance
from alphalab.strategy import (
    PythonStrategyError,
    StrategyRepository,
    TimingStrategyConfig,
    TimingStrategyRepository,
    execute_python_strategy,
    validate_python_source,
)
from alphalab.timing import evaluate_timing_signals


SELECTION_SOURCE = """\
def generate(context):
    symbols = sorted(item["symbol"] for item in context["candidates"])
    selected = symbols[-context["limits"]["max_stocks"]:]
    return {"weights": {symbol: 0.5 for symbol in selected}}
"""

TIMING_SOURCE = """\
def generate(context):
    values = [row["value"] for row in context["market_returns"]]
    exposure = context["limits"]["max_exposure"] if sum(values[-2:]) >= 0 else context["limits"]["min_exposure"]
    return {"market_exposure": exposure}
"""


def _selection_config(name: str = "python_selection") -> StrategyConfig:
    return StrategyConfig.from_dict(
        {
            "strategy_type": "stock_selection",
            "name": name,
            "universe": {
                "symbols": ["AAA", "BBB", "CCC"],
                "min_history_days": 20,
            },
            "factors": [],
            "selection": {"n_stocks": 2},
            "portfolio": {"max_weight": 0.5},
            "implementation": {
                "kind": "python",
                "entrypoint": "generate",
                "timeout_seconds": 3.0,
            },
        }
    )


def _timing_config(name: str = "python_timing") -> TimingStrategyConfig:
    return TimingStrategyConfig.from_dict(
        {
            "strategy_type": "market_timing",
            "name": name,
            "signals": [],
            "position": {"min_exposure": 0.2, "max_exposure": 0.8},
            "implementation": {
                "kind": "python",
                "entrypoint": "generate",
                "timeout_seconds": 3.0,
            },
        }
    )


def _write_market_fixture(root) -> None:
    market = root / "market"
    fundamentals = root / "fundamentals"
    factors = root / "factors"
    market.mkdir()
    fundamentals.mkdir()
    factors.mkdir()
    dates = pd.bdate_range("2023-01-02", "2023-05-31")
    rows = []
    for index, symbol in enumerate(("AAA", "BBB", "CCC")):
        close = 10.0 + index + np.linspace(0.0, 2.0 + index, len(dates))
        for date, price in zip(dates, close):
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 1_000.0,
                    "amount": price * 1_000.0,
                }
            )
    pd.DataFrame(rows).to_parquet(market / "bars.parquet")
    pd.DataFrame(columns=["quarter", "symbol"]).to_parquet(
        fundamentals / "fundamentals.parquet"
    )
    pd.DataFrame(index=pd.DatetimeIndex([], name="date")).to_parquet(
        factors / "factor_returns.parquet"
    )


def test_python_runtime_validates_executes_and_times_out() -> None:
    validation = validate_python_source(
        "def generate(context):\n    return {'value': context['value'] * 2}\n"
    )
    execution = execute_python_strategy(
        "def generate(context):\n    return {'value': context['value'] * 2}\n",
        "generate",
        [{"value": 3}],
        3.0,
    )

    assert validation["sha256"] == execution.source_sha256
    assert execution.values == [{"value": 6}]
    with pytest.raises(PythonStrategyError, match="must define def generate"):
        validate_python_source("value = 1\n")
    with pytest.raises(PythonStrategyError, match="timeout"):
        execute_python_strategy(
            "def generate(context):\n    while True:\n        pass\n",
            "generate",
            [{}],
            0.2,
        )


def test_python_selection_receives_point_in_time_candidates_and_enforces_limits(
    tmp_path,
) -> None:
    _write_market_fixture(tmp_path)
    engine = SignalEngine(create_default_engine(tmp_path))
    config = _selection_config()

    targets = engine.generate_targets(
        config,
        "2023-05-31",
        python_source=SELECTION_SOURCE,
    )

    assert targets == {"BBB": 0.5, "CCC": 0.5}
    assert engine.diagnostics["implementation"] == "python"
    assert engine.selection_snapshot["selected_count"] == 2
    assert engine.selection_snapshot["python"]["source_sha256"]
    with pytest.raises(ValueError, match="ineligible symbol"):
        engine.generate_targets(
            config,
            "2023-05-31",
            python_source=(
                "def generate(context):\n"
                "    return {'weights': {'NOT_ELIGIBLE': 0.5}}\n"
            ),
        )


def test_python_timing_returns_bounded_exposure_with_runtime_audit() -> None:
    market = pd.Series(
        [0.02, 0.01, -0.10, -0.05],
        index=pd.date_range("2024-01-31", periods=4, freq="M"),
    )

    scores = evaluate_timing_signals(_timing_config(), market, TIMING_SOURCE)

    assert scores["exposure"].tolist() == [0.8, 0.8, 0.2, 0.2]
    assert scores.attrs["python"]["source_sha256"]
    with pytest.raises(ValueError, match="must stay within"):
        evaluate_timing_signals(
            _timing_config(),
            market,
            "def generate(context):\n    return {'market_exposure': 1.0}\n",
        )


def test_python_strategy_source_is_saved_cloned_and_deleted(tmp_path) -> None:
    selection_repository = StrategyRepository(tmp_path / "selection")
    selection = selection_repository.save(
        "python_selection",
        _selection_config().to_yaml(),
        python_source=SELECTION_SOURCE,
    )
    selection_detail = selection.as_dict(include_yaml=True)

    assert selection.path.with_suffix(".py").read_text(encoding="utf-8") == SELECTION_SOURCE
    assert selection_detail["python_source"] == SELECTION_SOURCE
    assert selection_detail["python_source_sha256"]
    selection_clone = selection_repository.clone("python_selection", "python_selection_copy")
    assert selection_clone.path.with_suffix(".py").read_text(encoding="utf-8") == SELECTION_SOURCE
    assert selection_repository.delete("python_selection_copy") is True
    assert not selection_clone.path.with_suffix(".py").exists()

    timing_repository = TimingStrategyRepository(tmp_path / "timing")
    timing = timing_repository.save(
        "python_timing",
        _timing_config().to_yaml(),
        python_source=TIMING_SOURCE,
    )
    timing_clone = timing_repository.clone("python_timing", "python_timing_copy")

    assert timing.as_dict(include_yaml=True)["python_source"] == TIMING_SOURCE
    assert timing_clone.path.with_suffix(".py").read_text(encoding="utf-8") == TIMING_SOURCE
    assert timing_repository.delete("python_timing_copy") is True
    assert not timing_clone.path.with_suffix(".py").exists()


def test_python_source_is_part_of_research_provenance() -> None:
    provenance = build_research_provenance(
        "demo",
        _selection_config().to_yaml(),
        strategy_python=SELECTION_SOURCE,
    )

    assert provenance["strategy_python_sha256"]
    assert provenance["strategy_python"] == {
        "source": SELECTION_SOURCE,
        "sha256": provenance["strategy_python_sha256"],
    }
