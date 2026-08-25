"""Immutable, inspectable Python presets for every pipeline stage."""
from __future__ import annotations

from typing import Any


SELECTION_FACTOR_TOP = '''def select_assets(context):
    """Combine normalized factors and apply a rank-based holding buffer."""
    minimum = float(context["parameters"].get("min_factor_coverage", 0.5))
    count = int(context["parameters"].get("count", 20))
    exit_rank = max(count, int(context["parameters"].get("exit_rank", count)))
    names = list(context.get("factor_names") or [])
    scores = {}
    for candidate in context["candidates"]:
        values = candidate.get("factor_scores") or {}
        coverage = len(values) / len(names) if names else 1.0
        if coverage >= minimum:
            scores[candidate["symbol"]] = float(sum(values.values()))
    ranked = sorted(scores, key=scores.get, reverse=True)
    current = context.get("current_weights") or {}
    incumbents = [
        symbol for rank, symbol in enumerate(ranked, start=1)
        if rank <= exit_rank and float(current.get(symbol, 0.0)) > 0.0
    ]
    selected = incumbents[:count]
    for symbol in ranked:
        if len(selected) >= count:
            break
        if symbol not in selected:
            selected.append(symbol)
    selected.sort(key=scores.get, reverse=True)
    return {"selected": selected, "scores": scores}
'''

SELECTION_PASS_THROUGH = '''def select_assets(context):
    """Pass the first eligible base instrument through unchanged."""
    symbols = [item["symbol"] for item in context["candidates"]]
    return {"selected": symbols[:1], "scores": {symbol: 1.0 for symbol in symbols[:1]}}
'''

PORTFOLIO_EQUAL_WEIGHT = '''def construct_portfolio(context):
    """Turn the selected ranking into constrained long-only target weights."""
    selected = list(context["selection"].get("selected", []))
    scores = dict(context["selection"].get("scores", {}))
    method = str(context["parameters"].get("optimizer", "equal_weight"))
    maximum = float(context["parameters"].get("max_weight", 0.1))
    gross_limit = float(context["parameters"].get("max_gross_exposure", 1.0))
    rank_decay = float(context["parameters"].get("rank_decay", 1.0))
    if method == "score_weight" and selected:
        finite_scores = [float(scores.get(symbol, 0.0)) for symbol in selected]
        floor = min(finite_scores)
        offset = -min(floor, 0.0)
        strengths = {
            symbol: max(float(scores.get(symbol, 0.0)) + offset, 0.0) + 1e-12
            for symbol in selected
        }
    elif method == "rank_decay":
        strengths = {
            symbol: 1.0 / ((index + 1) ** max(rank_decay, 0.0))
            for index, symbol in enumerate(selected)
        }
    else:
        method = "equal_weight"
        strengths = {symbol: 1.0 for symbol in selected}

    budget = min(gross_limit, maximum * len(selected))
    weights = {}
    remaining = list(selected)
    while remaining and budget > 1e-12:
        total_strength = sum(strengths[symbol] for symbol in remaining)
        if total_strength <= 0.0:
            total_strength = float(len(remaining))
            active_strengths = {symbol: 1.0 for symbol in remaining}
        else:
            active_strengths = strengths
        capped = [
            symbol for symbol in remaining
            if budget * active_strengths[symbol] / total_strength >= maximum
        ]
        if not capped:
            for symbol in remaining:
                weights[symbol] = budget * active_strengths[symbol] / total_strength
            budget = 0.0
            break
        for symbol in capped:
            weights[symbol] = maximum
            budget -= maximum
        remaining = [symbol for symbol in remaining if symbol not in capped]

    weights = {symbol: weight for symbol, weight in weights.items() if weight > 0.0}
    return {
        "weights": weights,
        "gross_exposure": sum(weights.values()),
        "cash_weight": max(0.0, 1.0 - sum(weights.values())),
        "optimizer": method,
    }
'''

EXECUTION_FIXED = '''def configure_execution(context):
    """Describe guarded fill, liquidity, and transaction-cost assumptions."""
    parameters = context["parameters"]
    return {"execution": {
        "execution_price": parameters.get("execution_price", "next_open"),
        "cost_bps": float(parameters.get("cost_bps", 20.0)),
        "slippage_bps": float(parameters.get("slippage_bps", 0.0)),
        "impact_bps": float(parameters.get("impact_bps", 0.0)),
        "max_participation_rate": float(parameters.get("max_participation_rate", 0.1)),
        "portfolio_value": float(parameters.get("portfolio_value", 1000000.0)),
    }}
'''


BUILTIN_COMPONENTS: tuple[dict[str, Any], ...] = (
    {"id": "selection-factor-top", "stage": "selection", "name": "多因子综合排名", "description": "按决策频率归一化并组合多因子，使用进出排名缓冲生成信号集合", "source": SELECTION_FACTOR_TOP, "parameters": {"count": 20, "exit_rank": 30, "min_factor_coverage": 0.5, "signal_frequency": "monthly", "normalization": "percentile_rank", "factor_weights": {}}},
    {"id": "selection-pass-through", "stage": "selection", "name": "基础标的直通", "description": "保留研究范围中的第一个合格标的", "source": SELECTION_PASS_THROUGH, "parameters": {}},
    {"id": "portfolio-equal-weight", "stage": "portfolio", "name": "信号仓位分配", "description": "按等权、综合得分或排名衰减生成目标权重，并限制单票权重与总敞口", "source": PORTFOLIO_EQUAL_WEIGHT, "parameters": {"optimizer": "equal_weight", "rank_decay": 1.0, "max_weight": 0.1, "max_gross_exposure": 1.0}},
    {"id": "execution-monthly", "stage": "execution", "name": "下一交易日成交", "description": "信号形成后按下一交易日价格、流动性与成本假设成交", "source": EXECUTION_FIXED, "parameters": {"execution_price": "next_open", "cost_bps": 20.0, "slippage_bps": 0.0, "impact_bps": 0.0, "max_participation_rate": 0.1, "portfolio_value": 1000000.0}},
    {"id": "execution-daily", "stage": "execution", "name": "下一交易日成交（兼容）", "description": "保留旧项目组件标识；信号频率现在由信号模型设置", "source": EXECUTION_FIXED, "parameters": {"execution_price": "next_open", "cost_bps": 20.0, "slippage_bps": 0.0, "impact_bps": 0.0, "max_participation_rate": 0.1, "portfolio_value": 1000000.0}},
)

DEFAULT_PROJECT = {
    "id": "three-stage-default",
    "name": "三阶段默认策略",
    "description": "数据研究范围 → 月频多因子信号 → 约束等权 → 下一交易日执行",
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
