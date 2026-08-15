"""Read-only adapter for backtests saved before the Python pipeline contract."""
from __future__ import annotations

from typing import Any

import yaml

from alphalab.strategy import StrategyConfig


def legacy_config(yaml_text: str) -> StrategyConfig:
    raw = _load(yaml_text)
    if raw.get("strategy_type") != "market_timing":
        return StrategyConfig.from_dict(raw)

    position = dict(raw.get("position") or {})
    execution = dict(raw.get("execution") or {})
    universe = dict(raw.get("universe") or {})
    universe.setdefault("pool", "all")
    universe.setdefault("symbols", [])
    return StrategyConfig.from_dict(
        {
            "name": str(raw.get("name") or "legacy-timing"),
            "description": str(raw.get("description") or ""),
            "universe": universe,
            "factors": [],
            "selection": {"n_stocks": 1, "min_factor_coverage": 0.0},
            "portfolio": {
                "max_weight": float(position.get("max_exposure", 1.0)),
                "rebalance_freq": "monthly",
            },
            "execution": {
                "cost_bps": float(execution.get("cost_bps", 0.0)),
                "slippage_bps": float(execution.get("slippage_bps", 0.0)),
                "impact_bps": float(execution.get("impact_bps", 0.0)),
                "execution_price": "next_close",
                "portfolio_value": float(execution.get("portfolio_value", 1_000_000.0)),
                "max_participation_rate": float(execution.get("max_participation_rate", 0.1)),
            },
            "metadata": {
                "legacy_strategy_type": "market_timing",
                "legacy_market_factor": str(raw.get("market_factor") or "MKT"),
                "research_trials": int((raw.get("metadata") or {}).get("research_trials", 1)),
            },
        }
    )


def legacy_snapshot(yaml_text: str) -> dict[str, Any]:
    raw = _load(yaml_text)
    timing = raw.get("strategy_type") == "market_timing"
    execution = dict(raw.get("execution") or {})
    position = dict(raw.get("position") or {})
    portfolio = dict(raw.get("portfolio") or {})
    return {
        "strategy_type": "legacy_snapshot",
        "name": str(raw.get("name") or "Legacy strategy"),
        "description": str(raw.get("description") or "只读历史策略快照"),
        "factors": [
            str(item.get("name"))
            for item in raw.get("factors") or []
            if isinstance(item, dict) and item.get("name")
        ],
        "signals": [
            str(item.get("name") or item.get("kind") or "timing signal")
            for item in raw.get("signals") or []
            if isinstance(item, dict)
        ],
        "market_factor": str(raw.get("market_factor") or "MKT") if timing else None,
        "rebalance_freq": "monthly" if timing else portfolio.get("rebalance_freq", "monthly"),
        "execution_price": "historical snapshot",
        "cost_bps": float(execution.get("cost_bps", 0.0)),
        "max_weight": float(
            position.get("max_exposure", 1.0) if timing else portfolio.get("max_weight", 1.0)
        ),
        "legacy_format": "yaml",
    }


def _load(yaml_text: str) -> dict[str, Any]:
    raw = yaml.safe_load(yaml_text) or {}
    if not isinstance(raw, dict):
        raise ValueError("legacy strategy snapshot must contain a mapping")
    return raw


__all__ = ["legacy_config", "legacy_snapshot"]
