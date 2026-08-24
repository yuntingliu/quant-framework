"""Immutable, inspectable Python presets for every pipeline stage."""
from __future__ import annotations

from typing import Any


SELECTION_FACTOR_TOP = '''def select_assets(context):
    """Rank eligible candidates from the project-level stock pool."""
    minimum = float(context["parameters"].get("min_factor_coverage", 0.5))
    count = int(context["parameters"].get("count", 20))
    names = list(context.get("factor_names") or [])
    scores = {}
    for candidate in context["candidates"]:
        values = candidate.get("factor_scores") or {}
        coverage = len(values) / len(names) if names else 1.0
        if coverage >= minimum:
            scores[candidate["symbol"]] = float(sum(values.values()))
    selected = sorted(scores, key=scores.get, reverse=True)[:count]
    return {"selected": selected, "scores": scores}
'''

SELECTION_PASS_THROUGH = '''def select_assets(context):
    """Pass the first eligible base instrument through unchanged."""
    symbols = [item["symbol"] for item in context["candidates"]]
    return {"selected": symbols[:1], "scores": {symbol: 1.0 for symbol in symbols[:1]}}
'''

PORTFOLIO_EQUAL_WEIGHT = '''def construct_portfolio(context):
    """Allocate equal weight subject to single-name and gross-exposure limits."""
    selected = list(context["selection"].get("selected", []))
    maximum = float(context["parameters"].get("max_weight", 0.1))
    gross_limit = float(context["parameters"].get("max_gross_exposure", 1.0))
    weight = min(maximum, gross_limit / len(selected)) if selected else 0.0
    weights = {symbol: weight for symbol in selected if weight > 0.0}
    return {
        "weights": weights,
        "gross_exposure": sum(weights.values()),
        "cash_weight": max(0.0, 1.0 - sum(weights.values())),
    }
'''

EXECUTION_FIXED = '''def configure_execution(context):
    """Describe fixed-calendar rebalancing and guarded execution assumptions."""
    parameters = context["parameters"]
    return {"execution": {
        "rebalance_freq": parameters.get("rebalance_freq", "monthly"),
        "execution_price": parameters.get("execution_price", "next_open"),
        "cost_bps": float(parameters.get("cost_bps", 20.0)),
        "slippage_bps": float(parameters.get("slippage_bps", 0.0)),
        "impact_bps": float(parameters.get("impact_bps", 0.0)),
        "max_participation_rate": float(parameters.get("max_participation_rate", 0.1)),
        "portfolio_value": float(parameters.get("portfolio_value", 1000000.0)),
    }}
'''


BUILTIN_COMPONENTS: tuple[dict[str, Any], ...] = (
    {"id": "selection-factor-top", "stage": "selection", "name": "因子排名选股", "description": "按横截面复合信号选择前 N 名", "source": SELECTION_FACTOR_TOP, "parameters": {"count": 20, "min_factor_coverage": 0.5}},
    {"id": "selection-pass-through", "stage": "selection", "name": "基础标的直通", "description": "保留股票池中的第一个合格标的", "source": SELECTION_PASS_THROUGH, "parameters": {}},
    {"id": "portfolio-equal-weight", "stage": "portfolio", "name": "约束等权组合", "description": "对选中标的等权并限制单票权重与总敞口", "source": PORTFOLIO_EQUAL_WEIGHT, "parameters": {"max_weight": 0.1, "max_gross_exposure": 1.0}},
    {"id": "execution-monthly", "stage": "execution", "name": "月度调仓", "description": "月末信号后下一交易日执行", "source": EXECUTION_FIXED, "parameters": {"rebalance_freq": "monthly", "execution_price": "next_open", "cost_bps": 20.0, "slippage_bps": 0.0, "impact_bps": 0.0, "max_participation_rate": 0.1, "portfolio_value": 1000000.0}},
    {"id": "execution-daily", "stage": "execution", "name": "日度调仓", "description": "每日收盘信号后下一交易日执行", "source": EXECUTION_FIXED, "parameters": {"rebalance_freq": "daily", "execution_price": "next_open", "cost_bps": 20.0, "slippage_bps": 0.0, "impact_bps": 0.0, "max_participation_rate": 0.1, "portfolio_value": 1000000.0}},
)

DEFAULT_PROJECT = {
    "id": "three-stage-default",
    "name": "三阶段默认策略",
    "description": "项目股票池 → 因子前20 → 约束等权 → 月度执行",
    "components": {
        "selection": {"component_id": "selection-factor-top", "version": 1},
        "portfolio": {"component_id": "portfolio-equal-weight", "version": 1},
        "execution": {"component_id": "execution-monthly", "version": 1},
    },
    "settings": {
        "universe": {"pool": "all", "symbols": [], "min_price": 0.0, "min_history_days": 60, "min_average_amount": 0.0, "max_stale_days": 7, "require_positive_volume": True},
        "factors": [{"name": "momentum_20d", "weight": 1.0, "direction": "long", "source": "technical", "winsorize": 0.01, "neutralize": []}],
        "lookback_days": 120,
        "research_thresholds": {"min_sharpe": 0.5, "max_drawdown": -0.35, "max_turnover": 1.0},
    },
}


__all__ = ["BUILTIN_COMPONENTS", "DEFAULT_PROJECT"]
