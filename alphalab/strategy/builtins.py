"""Canonical SDK v1 strategy templates."""

from __future__ import annotations

DEFAULT_STRATEGY_SOURCE = '''"""SDK v1 教学策略：月末选择动量最高的标的并在下一交易日开盘成交。"""

from typing import Annotated

from alphalab.sdk.v1 import (
    Event,
    ExecutionPolicy,
    Monthly,
    Parameter,
    PortfolioDecision,
    SignalResult,
    UniverseResult,
    execution,
    factor,
    on_event,
    portfolio,
    signal,
    universe,
)

SDK_VERSION = 1

# 保存时系统会核对声明；运行前会拒绝缺少这些字段的数据环境。
DATA_REQUIREMENTS = {
    "bars": ["open", "high", "low", "close", "volume", "amount"],
}


@universe(id="research_universe", label="研究标的池")
def research_universe(context):
    """使用当前数据环境已经按日期过滤好的研究标的池。"""
    # UniverseResult 保留顺序，后续因子和信号只在这些证券上运行。
    return UniverseResult(symbols=context.universe)


@factor(id="momentum_20d", label="20 日动量")
def momentum_20d(
    context,
    *,
    window: Annotated[
        int,
        Parameter(label="窗口", minimum=2, maximum=500, step=1),
    ] = 20,
):
    """返回截至当前评估日的 window 日累计收益率横截面。"""
    # 多读取一个收盘价，才能形成完整的 window 段收益。
    close = context.history("close", window=window + 1)
    if len(close.index) < window + 1:
        # 历史不足时返回同列结构的 NaN，不用短窗口制造信号。
        return close.mean(axis=0) * float("nan")
    return close.iloc[-1] / close.iloc[0] - 1.0


@signal(
    id="monthly_momentum",
    label="月末动量 Top N",
    schedule=Monthly.last_trading_day(at="close"),
)
def monthly_momentum(context, state, *, top_n: int = 10):
    """在每月最后一个交易日收盘后，选择动量最高的 top_n 个标的。"""
    # combine_factors 会复用上方已保存因子；rank/zscore 也可在这里选择。
    scores = context.combine_factors(
        weights={"momentum_20d": 1.0},
        normalization="raw",
        parameters={"momentum_20d": {"window": 20}},
    ).dropna()
    # selected 的顺序会保留到组合阶段，也用于运行结果中的信号解释。
    selected = list(scores.nlargest(top_n).index)
    return SignalResult(selected=selected, scores=scores, state=state)


@portfolio(id="equal_weight", label="等权配置")
def equal_weight(context, signal, state, *, max_weight: float = 0.10):
    """把入选标的转换为受单标的上限约束的目标权重。"""
    selected = list(signal.selected)
    # 没有候选时保持全现金；候选较少时未分配部分同样留在现金中。
    weight = min(max_weight, 1.0 / len(selected)) if selected else 0.0
    return PortfolioDecision(
        target_weights={symbol: weight for symbol in selected},
        state=state,
    )


@on_event(Event.SESSION_CLOSE, id="holding_period_risk", label="自定义持有期风控")
def holding_period_risk(context, state):
    """展示每日持有期风控入口；默认模板不主动改仓。"""
    # 可读取 context.portfolio 的真实持仓；返回 PortfolioDecision 才会调整目标仓位。
    # 跨日变量应写入 state 并随返回对象带回，不要使用模块全局变量。
    return None


@execution(id="next_open", label="下一交易日开盘成交")
def next_open(
    context,
    decision,
    *,
    commission_rate: float = 0.00025,
    slippage_rate: float = 0.00020,
    max_participation_rate: float = 0.10,
):
    """声明目标仓位在下一交易日开盘按容量和成本约束尝试成交。"""
    # 收盘信号不能偷用同一收盘价；next_session_open 明确隔离信号与成交时点。
    return ExecutionPolicy(
        activation="next_session_open",
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
        max_participation_rate=max_participation_rate,
    )
'''


# This compact example demonstrates daily stateful exits; it is intentionally
# not labeled as the user's full 127-instrument V10 strategy package.
ETF_ROTATION_EVENT_EXAMPLE_SOURCE = '''"""SDK v1 事件示例：月末 ETF 轮动，并在持有期每日执行保护规则。"""

from alphalab.sdk.v1 import (
    Event,
    ExecutionPolicy,
    Monthly,
    PortfolioDecision,
    SignalResult,
    UniverseResult,
    execution,
    factor,
    on_event,
    portfolio,
    signal,
    universe,
)

SDK_VERSION = 1

# 日线用于因子、均线、估值和下一开盘成交；amount 支持容量约束。
DATA_REQUIREMENTS = {
    "bars": ["open", "high", "low", "close", "volume", "amount"],
}

ETF_POOL = ("510300", "512100", "513100", "511260")
DEFENSIVE = "511260"


@universe(id="major_etfs")
def major_etfs(context):
    """只保留示例 ETF 池中当前数据环境实际可用的证券。"""
    available = set(context.universe)
    # 先与点时标的池求交集，避免把未上市或无数据 ETF 强行加入回测。
    return UniverseResult(symbols=[symbol for symbol in ETF_POOL if symbol in available])


@factor(id="momentum_20d")
def momentum_20d(context, *, window: int = 20):
    """计算截至当前评估日的短期累计收益率。"""
    close = context.history("close", window=window + 1)
    if len(close.index) < window + 1:
        # 新上市或数据不足时保留 NaN，后续信号会自动跳过。
        return close.mean(axis=0) * float("nan")
    return close.iloc[-1] / close.iloc[0] - 1.0


@signal(id="monthly_top1", schedule=Monthly.last_trading_day(at="close"))
def monthly_top1(context, state, *, top_n: int = 1, minimum_momentum: float = 0.03):
    """月末解除保护锁，并选择达到最低动量门槛的风险资产。"""
    # 新调仓月允许重新进入风险资产；保护事件可在月内再次锁定。
    state["locked_until_month_end"] = False
    # 防御资产不参加风险资产排名，但仍可由组合阶段作为兜底持仓。
    scores = context.factor("momentum_20d", window=20).drop(labels=[DEFENSIVE], errors="ignore").dropna()
    scores = scores[scores >= minimum_momentum]
    selected = list(scores.nlargest(top_n).index)
    return SignalResult(selected=selected, scores=scores, state=state)


@portfolio(id="top1_or_bond")
def top1_or_bond(context, signal, state):
    """持有排名第一的风险资产；没有合格候选时全仓防御资产。"""
    target = signal.selected[0] if signal.selected else DEFENSIVE
    return PortfolioDecision(target_weights={target: 1.0}, state=state)


@on_event(Event.SESSION_CLOSE, id="daily_protection")
def daily_protection(context, state):
    """每日收盘检查均线和利润回撤，并可切换到防御资产。"""
    holding = context.portfolio.primary_holding
    # 没有风险持仓或本月已经触发保护时，无需重复生成目标仓位。
    if holding is None or holding.symbol == DEFENSIVE or state.get("locked_until_month_end"):
        return None
    # 峰值收益保存在显式 state 中，确保不同 Run 和 worker 互不污染。
    peak = max(float(state.get("peak_return", 0.0)), float(holding.return_since_entry))
    state["peak_return"] = peak
    close = context.history("close", symbols=[holding.symbol], window=100)
    below_ma100 = holding.close is not None and holding.close < float(close[holding.symbol].mean())
    profit_lock = peak > 0.20 and peak - holding.return_since_entry > 0.08
    if below_ma100 or profit_lock:
        # 一旦触发，本月锁定在防御资产；下个月信号入口会解除锁定。
        state["locked_until_month_end"] = True
        return PortfolioDecision(
            target_weights={DEFENSIVE: 1.0},
            state=state,
            reason="ma100_gate" if below_ma100 else "profit_lock",
        )
    return None


@execution(id="next_open")
def next_open(context, decision):
    """在下一交易日开盘执行，并显式计入佣金与滑点。"""
    return ExecutionPolicy(
        activation="next_session_open",
        commission_rate=0.00025,
        slippage_rate=0.0002,
    )
'''


__all__ = ["DEFAULT_STRATEGY_SOURCE", "ETF_ROTATION_EVENT_EXAMPLE_SOURCE"]
