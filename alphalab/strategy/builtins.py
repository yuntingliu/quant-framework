"""Canonical SDK v1 strategy templates."""

from __future__ import annotations

DEFAULT_STRATEGY_SOURCE = """from typing import Annotated

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

DATA_REQUIREMENTS = {
    "bars": ["open", "high", "low", "close", "volume", "amount"],
}


@universe(id="research_universe", label="研究标的池")
def research_universe(context):
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
    close = context.history("close", window=window + 1)
    return close.iloc[-1] / close.iloc[0] - 1.0


@signal(
    id="monthly_momentum",
    label="月末动量 Top N",
    schedule=Monthly.last_trading_day(at="close"),
)
def monthly_momentum(context, state, *, top_n: int = 10):
    scores = context.combine_factors(
        weights={"momentum_20d": 1.0},
        normalization="raw",
        parameters={"momentum_20d": {"window": 20}},
    ).dropna()
    selected = list(scores.nlargest(top_n).index)
    return SignalResult(selected=selected, scores=scores, state=state)


@portfolio(id="equal_weight", label="等权配置")
def equal_weight(context, signal, state, *, max_weight: float = 0.10):
    selected = list(signal.selected)
    weight = min(max_weight, 1.0 / len(selected)) if selected else 0.0
    return PortfolioDecision(
        target_weights={symbol: weight for symbol in selected},
        state=state,
    )


@on_event(Event.SESSION_CLOSE, id="holding_period_risk", label="自定义持有期风控")
def holding_period_risk(context, state):
    # 在每日收盘检查持仓；返回 PortfolioDecision 可调整目标仓位。
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
    return ExecutionPolicy(
        activation="next_session_open",
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
        max_participation_rate=max_participation_rate,
    )
"""


# This compact example demonstrates daily stateful exits; it is intentionally
# not labeled as the user's full 127-instrument V10 strategy package.
ETF_ROTATION_EVENT_EXAMPLE_SOURCE = """from alphalab.sdk.v1 import (
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

DATA_REQUIREMENTS = {
    "bars": ["open", "high", "low", "close", "volume", "amount"],
}

ETF_POOL = ("510300", "512100", "513100", "511260")
DEFENSIVE = "511260"


@universe(id="major_etfs")
def major_etfs(context):
    available = set(context.universe)
    return UniverseResult(symbols=[symbol for symbol in ETF_POOL if symbol in available])


@factor(id="momentum_20d")
def momentum_20d(context, *, window: int = 20):
    close = context.history("close", window=window + 1)
    return close.iloc[-1] / close.iloc[0] - 1.0


@signal(id="monthly_top1", schedule=Monthly.last_trading_day(at="close"))
def monthly_top1(context, state, *, top_n: int = 1, minimum_momentum: float = 0.03):
    state["locked_until_month_end"] = False
    scores = context.factor("momentum_20d", window=20).drop(labels=[DEFENSIVE], errors="ignore").dropna()
    scores = scores[scores >= minimum_momentum]
    selected = list(scores.nlargest(top_n).index)
    return SignalResult(selected=selected, scores=scores, state=state)


@portfolio(id="top1_or_bond")
def top1_or_bond(context, signal, state):
    target = signal.selected[0] if signal.selected else DEFENSIVE
    return PortfolioDecision(target_weights={target: 1.0}, state=state)


@on_event(Event.SESSION_CLOSE, id="daily_protection")
def daily_protection(context, state):
    holding = context.portfolio.primary_holding
    if holding is None or holding.symbol == DEFENSIVE or state.get("locked_until_month_end"):
        return None
    peak = max(float(state.get("peak_return", 0.0)), float(holding.return_since_entry))
    state["peak_return"] = peak
    close = context.history("close", symbols=[holding.symbol], window=100)
    below_ma100 = holding.close is not None and holding.close < float(close[holding.symbol].mean())
    profit_lock = peak > 0.20 and peak - holding.return_since_entry > 0.08
    if below_ma100 or profit_lock:
        state["locked_until_month_end"] = True
        return PortfolioDecision(
            target_weights={DEFENSIVE: 1.0},
            state=state,
            reason="ma100_gate" if below_ma100 else "profit_lock",
        )
    return None


@execution(id="next_open")
def next_open(context, decision):
    return ExecutionPolicy(
        activation="next_session_open",
        commission_rate=0.00025,
        slippage_rate=0.0002,
    )
"""


__all__ = ["DEFAULT_STRATEGY_SOURCE", "ETF_ROTATION_EVENT_EXAMPLE_SOURCE"]
