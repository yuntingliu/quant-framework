"""Immutable, inspectable Python presets for every pipeline stage."""
from __future__ import annotations

from typing import Any


UNIVERSE_ALL = '''def build_universe(context):
    """Keep every point-in-time eligible asset from the configured base pool."""
    return {"symbols": [item["symbol"] for item in context["candidates"]]}
'''

UNIVERSE_WATCHLIST = '''def build_universe(context):
    """Intersect eligible assets with the symbols saved in this stage's parameters."""
    wanted = {str(value).strip().upper() for value in context["parameters"].get("symbols", [])}
    return {"symbols": [item["symbol"] for item in context["candidates"] if item["symbol"] in wanted]}
'''

SELECTION_FACTOR_TOP = '''def select_assets(context):
    """Rank the cross-sectional factor scores prepared at the decision date."""
    minimum = float(context["parameters"].get("min_factor_coverage", 0.5))
    count = int(context["parameters"].get("count", 20))
    names = list(context.get("factor_names") or [])
    scores = {}
    for candidate in context["candidates"]:
        if candidate["symbol"] not in set(context["universe_symbols"]):
            continue
        values = candidate.get("factor_scores") or {}
        coverage = len(values) / len(names) if names else 1.0
        if coverage >= minimum:
            scores[candidate["symbol"]] = float(sum(values.values()))
    selected = sorted(scores, key=scores.get, reverse=True)[:count]
    return {"selected": selected, "scores": scores}
'''

SELECTION_PASS_THROUGH = '''def select_assets(context):
    """Pass the base instrument through so a project can express pure timing."""
    symbols = list(context["universe_symbols"])
    return {"selected": symbols[:1], "scores": {symbol: 1.0 for symbol in symbols[:1]}}
'''

TIMING_ALWAYS_ON = '''def compute_exposure(context):
    """Identity timing overlay used by pure stock-selection projects."""
    return {"exposure": 1.0, "signal": "always_on"}
'''

TIMING_TREND = '''def compute_exposure(context):
    """Invest when cumulative market wealth is above its moving average."""
    window = int(context["parameters"].get("window", 10))
    floor = float(context["parameters"].get("min_exposure", 0.0))
    ceiling = float(context["parameters"].get("max_exposure", 1.0))
    returns = [float(row["value"]) for row in context.get("market_returns", [])]
    if len(returns) < max(2, window):
        return {"exposure": floor, "signal": "insufficient_history"}
    wealth = []
    value = 1.0
    for item in returns:
        value *= 1.0 + item
        wealth.append(value)
    average = sum(wealth[-window:]) / window
    active = wealth[-1] > average
    return {"exposure": ceiling if active else floor, "signal": "trend"}
'''

PORTFOLIO_EQUAL_WEIGHT = '''def construct_portfolio(context):
    """Allocate equal weight to selected assets, scaled by the timing overlay."""
    selected = list(context["selection"].get("selected", []))
    exposure = float(context["timing"].get("exposure", 1.0))
    weight = exposure / len(selected) if selected else 0.0
    return {"weights": {symbol: weight for symbol in selected}}
'''

RISK_CONCENTRATION = '''def apply_risk(context):
    """Apply gross-exposure and single-name caps without creating new assets."""
    maximum = float(context["parameters"].get("max_weight", 0.1))
    gross_limit = float(context["parameters"].get("max_gross_exposure", 1.0))
    proposed = {symbol: max(0.0, float(weight)) for symbol, weight in context["portfolio"]["weights"].items()}
    capped = {symbol: min(maximum, weight) for symbol, weight in proposed.items() if weight > 0.0}
    total = sum(capped.values())
    if total > gross_limit and total > 0.0:
        scale = gross_limit / total
        capped = {symbol: weight * scale for symbol, weight in capped.items()}
    return {"weights": capped, "gross_exposure": sum(capped.values())}
'''

EXECUTION_MONTHLY = '''def create_orders(context):
    """Describe monthly next-open rebalancing; the core applies market and cash gates."""
    parameters = context["parameters"]
    return {"execution": {
        "rebalance_freq": "monthly",
        "execution_price": parameters.get("execution_price", "next_open"),
        "cost_bps": float(parameters.get("cost_bps", 20.0)),
        "slippage_bps": float(parameters.get("slippage_bps", 0.0)),
        "impact_bps": float(parameters.get("impact_bps", 0.0)),
        "max_participation_rate": float(parameters.get("max_participation_rate", 0.1)),
        "portfolio_value": float(parameters.get("portfolio_value", 1000000.0)),
    }}
'''


BUILTIN_COMPONENTS: tuple[dict[str, Any], ...] = (
    {"id": "universe-all", "stage": "universe", "name": "全部合格标的", "description": "使用基础池中当期可交易的全部标的", "source": UNIVERSE_ALL, "parameters": {}},
    {"id": "universe-watchlist", "stage": "universe", "name": "自选标的池", "description": "合格标的与自选列表取交集", "source": UNIVERSE_WATCHLIST, "parameters": {"symbols": ["000001.XSHE"]}},
    {"id": "selection-factor-top", "stage": "selection", "name": "因子排名选股", "description": "按横截面复合信号选择前 N 名", "source": SELECTION_FACTOR_TOP, "parameters": {"count": 20, "min_factor_coverage": 0.5}},
    {"id": "selection-pass-through", "stage": "selection", "name": "基准标的直通", "description": "纯择时项目使用的恒等选股组件", "source": SELECTION_PASS_THROUGH, "parameters": {}},
    {"id": "timing-always-on", "stage": "timing", "name": "始终满仓", "description": "纯选股项目使用的恒等择时覆盖", "source": TIMING_ALWAYS_ON, "parameters": {}},
    {"id": "timing-trend", "stage": "timing", "name": "市场趋势择时", "description": "市场累计净值高于移动均线时持仓", "source": TIMING_TREND, "parameters": {"window": 10, "min_exposure": 0.0, "max_exposure": 1.0}},
    {"id": "portfolio-equal-weight", "stage": "portfolio", "name": "等权组合", "description": "对选中标的等权并叠加择时仓位", "source": PORTFOLIO_EQUAL_WEIGHT, "parameters": {}},
    {"id": "risk-concentration", "stage": "risk", "name": "集中度与总仓位", "description": "限制单标的权重与组合总敞口", "source": RISK_CONCENTRATION, "parameters": {"max_weight": 0.1, "max_gross_exposure": 1.0}},
    {"id": "execution-monthly", "stage": "execution", "name": "月度调仓", "description": "每月信号后下一交易日执行", "source": EXECUTION_MONTHLY, "parameters": {"execution_price": "next_open", "cost_bps": 20.0, "slippage_bps": 0.0, "impact_bps": 0.0, "max_participation_rate": 0.1, "portfolio_value": 1000000.0}},
)

DEFAULT_PROJECT = {
    "id": "six-stage-default",
    "name": "六阶段默认策略",
    "description": "全市场合格标的 → 因子前20 → 不择时 → 等权 → 集中度限制 → 月度执行",
    "components": {
        "universe": {"component_id": "universe-all", "version": 1},
        "selection": {"component_id": "selection-factor-top", "version": 1},
        "timing": {"component_id": "timing-always-on", "version": 1},
        "portfolio": {"component_id": "portfolio-equal-weight", "version": 1},
        "risk": {"component_id": "risk-concentration", "version": 1},
        "execution": {"component_id": "execution-monthly", "version": 1},
    },
    "settings": {
        "universe": {"pool": "all", "symbols": [], "min_price": 0.0, "min_history_days": 60, "min_average_amount": 0.0, "max_stale_days": 7, "require_positive_volume": True},
        "factors": [{"name": "momentum_20d", "weight": 1.0, "direction": "long", "source": "technical", "winsorize": 0.01, "neutralize": []}],
        "lookback_days": 120,
    },
}


__all__ = ["BUILTIN_COMPONENTS", "DEFAULT_PROJECT"]
