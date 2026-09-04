"""Canonical SDK v1 strategy templates."""

from __future__ import annotations

DEFAULT_STRATEGY_SOURCE = '''"""SDK v1 教学策略：月末从全市场选择动量最高的标的并在下一交易日开盘成交。"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated

import pandas as pd

from alphalab.sdk.v1 import (
    Event,
    ExecutionPolicy,
    Monthly,
    Parameter,
    PortfolioDecision,
    SignalResult,
    UniverseResult,
    execution,
    execution_data_fill,
    factor,
    on_event,
    portfolio,
    signal,
    universe,
)

SDK_VERSION = 1

# 保存时系统会核对声明；运行前会拒绝缺少这些字段的数据环境。
DATA_REQUIREMENTS = {
    "bars": [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    ],
}


@universe(id="research_universe", label="全市场点时有效股票池")
def research_universe(context):
    """默认使用当前日期已经上市、尚未退市且有有效行情的全部股票。"""
    # context.universe 已按日期过滤；不要用少量手写代码代替全市场股票池。
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


@execution_data_fill(id="fill_missing_market_state", label="补齐缺失交易状态")
def fill_missing_market_state(
    context,
    rows,
    *,
    main_board_limit_rate: Annotated[
        float,
        Parameter(label="主板涨跌幅", minimum=0.01, maximum=1.0, step=0.01),
    ] = 0.10,
    main_board_st_limit_rate_before_change: Annotated[
        float,
        Parameter(label="主板 ST 调整前涨跌幅", minimum=0.01, maximum=1.0, step=0.01),
    ] = 0.05,
    main_board_st_limit_rate: Annotated[
        float,
        Parameter(label="主板 ST 当前涨跌幅", minimum=0.01, maximum=1.0, step=0.01),
    ] = 0.10,
    main_board_st_change_date: Annotated[
        str,
        Parameter(label="主板 ST 规则切换日"),
    ] = "2026-07-06",
    star_market_limit_rate: Annotated[
        float,
        Parameter(label="科创板涨跌幅", minimum=0.01, maximum=1.0, step=0.01),
    ] = 0.20,
    chinext_limit_rate: Annotated[
        float,
        Parameter(label="创业板涨跌幅", minimum=0.01, maximum=1.0, step=0.01),
    ] = 0.20,
    beijing_limit_rate: Annotated[
        float,
        Parameter(label="北交所涨跌幅", minimum=0.01, maximum=1.0, step=0.01),
    ] = 0.30,
    ipo_unlimited_sessions: Annotated[
        int,
        Parameter(label="沪深新股无涨跌停交易日", minimum=0, maximum=30, step=1),
    ] = 5,
    beijing_ipo_unlimited_sessions: Annotated[
        int,
        Parameter(label="北交所新股无涨跌停交易日", minimum=0, maximum=30, step=1),
    ] = 1,
):
    """用成交日前的未复权行情和状态补齐实际订单所需的交易状态。"""
    filled = rows.copy()
    if filled.empty:
        return filled
    filled["symbol"] = filled["symbol"].astype(str).str.upper()
    symbols = list(dict.fromkeys(filled["symbol"]))

    # 停牌状态优先沿用上一交易日的已知值；没有历史值时继续保持缺失，交给严格引擎拒单。
    suspended = context.history("is_suspended", window=1, symbols=symbols)
    if not suspended.empty:
        previous_suspended = suspended.iloc[-1]
        missing = filled["is_suspended"].isna()
        filled.loc[missing, "is_suspended"] = filled.loc[missing, "symbol"].map(
            previous_suspended
        )

    # 涨跌停价必须按前一交易日未复权收盘价计算，不能使用策略因子的复权 close。
    raw_close = context.history("raw_close", window=1, symbols=symbols)
    previous_close = (
        raw_close.iloc[-1] if not raw_close.empty else pd.Series(index=symbols, dtype=float)
    )
    st_history = context.history("is_st", window=1, symbols=symbols)
    previous_is_st = (
        st_history.iloc[-1] if not st_history.empty else pd.Series(False, index=symbols)
    )

    instruments = context.instruments()
    listed_field = next(
        (field for field in ("listed_date", "list_date") if field in instruments),
        None,
    )
    listing_dates = pd.Series(dtype="datetime64[ns]")
    if listed_field is not None and "symbol" in instruments:
        listing_dates = pd.Series(
            pd.to_datetime(instruments[listed_field], errors="coerce").values,
            index=instruments["symbol"].astype(str).str.upper(),
        )

    sessions = tuple(
        session
        for session in context.calendar.sessions
        if pd.Timestamp(session).normalize() <= context.as_of
    )
    no_limit_symbols = set()
    limit_rates = {}
    for symbol in symbols:
        code, _, exchange = symbol.partition(".")
        is_beijing = exchange == "BJ" or code.startswith(("4", "8", "920"))
        is_star = exchange == "SH" and code.startswith(("688", "689"))
        is_chinext = exchange == "SZ" and code.startswith(("300", "301"))

        listed_date = listing_dates.get(symbol, pd.NaT)
        if pd.notna(listed_date):
            listed_date = pd.Timestamp(listed_date).normalize()
            listed_sessions = sum(listed_date <= session <= context.as_of for session in sessions)
            unlimited_sessions = (
                beijing_ipo_unlimited_sessions if is_beijing else ipo_unlimited_sessions
            )
            if 0 < listed_sessions <= unlimited_sessions:
                no_limit_symbols.add(symbol)

        if is_beijing:
            rate = beijing_limit_rate
        elif is_star:
            rate = star_market_limit_rate
        elif is_chinext:
            rate = chinext_limit_rate
        elif pd.notna(previous_is_st.get(symbol)) and bool(previous_is_st.get(symbol)):
            rate = (
                main_board_st_limit_rate_before_change
                if context.as_of < pd.Timestamp(main_board_st_change_date)
                else main_board_st_limit_rate
            )
        else:
            rate = main_board_limit_rate
        limit_rates[symbol] = rate

    # 一对 0 是 SDK 明确定义的“当日无涨跌停”标记；只有两列都缺失时才能写入。
    no_limit_rows = filled["symbol"].isin(no_limit_symbols)
    both_limits_missing = filled["limit_up"].isna() & filled["limit_down"].isna()
    filled.loc[no_limit_rows & both_limits_missing, ["limit_up", "limit_down"]] = 0.0

    rates = pd.Series(limit_rates, dtype=float)
    price_tick = Decimal("0.01")
    derived_up = (previous_close * (1.0 + rates)).map(
        lambda value: (
            float(Decimal(str(value)).quantize(price_tick, rounding=ROUND_HALF_UP))
            if pd.notna(value)
            else float("nan")
        )
    )
    derived_down = (previous_close * (1.0 - rates)).map(
        lambda value: (
            float(Decimal(str(value)).quantize(price_tick, rounding=ROUND_HALF_UP))
            if pd.notna(value)
            else float("nan")
        )
    )
    # 上市初期若只拿到单边价格，不伪造另一边；保留缺失让严格引擎明确拒单。
    missing_up = filled["limit_up"].isna() & ~no_limit_rows
    missing_down = filled["limit_down"].isna() & ~no_limit_rows
    filled.loc[missing_up, "limit_up"] = filled.loc[missing_up, "symbol"].map(derived_up)
    filled.loc[missing_down, "limit_down"] = filled.loc[missing_down, "symbol"].map(
        derived_down
    )
    return filled


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
