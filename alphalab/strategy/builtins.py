"""Canonical SDK v1 default strategy source."""

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
    "instruments": ["asset_type"],
}


@universe(id="research_universe", label="全市场点时有效股票池")
def research_universe(context):
    """默认使用当前日期已经上市、尚未退市且有有效行情的全部股票。"""
    # context.universe 是点时有效证券集合；这里明确只保留普通股票，避免混入 ETF。
    instruments = context.instruments()
    if instruments.empty or not {"symbol", "asset_type"}.issubset(instruments.columns):
        return UniverseResult(symbols=[])
    common_stocks = set(
        instruments.loc[
            instruments["asset_type"].astype(str).str.upper().eq("CS"), "symbol"
        ]
        .astype(str)
        .str.upper()
    )
    return UniverseResult(
        symbols=[symbol for symbol in context.universe if symbol in common_stocks]
    )


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
    """默认不补单：买不到留现金，卖不出保留原持仓，等下一次月度信号再调仓。"""
    # 每个目标仅在约定成交时点尝试一次。需要跨日重试时，在此显式返回新的 PortfolioDecision。
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
    etf_limit_rate: Annotated[
        float,
        Parameter(label="ETF 涨跌幅", minimum=0.01, maximum=1.0, step=0.01),
    ] = 0.10,
    ipo_unlimited_sessions: Annotated[
        int,
        Parameter(label="沪深新股无涨跌停交易日", minimum=0, maximum=30, step=1),
    ] = 5,
    beijing_ipo_unlimited_sessions: Annotated[
        int,
        Parameter(label="北交所新股无涨跌停交易日", minimum=0, maximum=30, step=1),
    ] = 1,
    state_lookback_sessions: Annotated[
        int,
        Parameter(label="状态补齐向前查找交易日数", minimum=1, maximum=120, step=1),
    ] = 120,
    fill_unknown_suspension_as_tradable: Annotated[
        bool,
        Parameter(label="无历史停牌状态时按可交易补齐"),
    ] = True,
    reference_price_lookback_sessions: Annotated[
        int,
        Parameter(label="未复权参考价向前查找交易日数", minimum=1, maximum=500, step=1),
    ] = 120,
):
    """用成交日前的未复权行情和状态补齐实际订单所需的交易状态。"""
    filled = rows.copy()
    if filled.empty:
        return filled
    filled["symbol"] = filled["symbol"].astype(str).str.upper()
    symbols = list(dict.fromkeys(filled["symbol"]))

    # 停牌状态优先沿用最近历史值；整个窗口都未知时由下方可编辑开关决定是否按未停牌补齐。
    suspended = context.history(
        "is_suspended", window=state_lookback_sessions, symbols=symbols
    )
    if not suspended.empty:
        previous_suspended = suspended.reindex(columns=symbols).ffill().iloc[-1]
        missing = filled["is_suspended"].isna()
        filled.loc[missing, "is_suspended"] = filled.loc[missing, "symbol"].map(
            previous_suspended
        )
    if fill_unknown_suspension_as_tradable:
        # 已有停牌状态永不覆盖；仅对整个回看窗口都没有记录的实际订单采用可见默认值。
        filled.loc[filled["is_suspended"].isna(), "is_suspended"] = False

    # 涨跌停价必须按前一交易日未复权收盘价计算，不能使用策略因子的复权 close。
    raw_close = context.history(
        "raw_close", window=reference_price_lookback_sessions, symbols=symbols
    )
    previous_close = (
        raw_close.reindex(columns=symbols).apply(pd.to_numeric, errors="coerce").ffill().iloc[-1]
        if not raw_close.empty
        else pd.Series(index=symbols, dtype=float)
    )
    previous_close = previous_close.where(previous_close.gt(0))
    st_history = context.history(
        "is_st", window=state_lookback_sessions, symbols=symbols
    )
    previous_is_st = (
        st_history.reindex(columns=symbols).ffill().iloc[-1]
        if not st_history.empty
        else pd.Series(False, index=symbols)
    )

    instruments = context.instruments()
    asset_types = pd.Series(dtype=str)
    if {"symbol", "asset_type"}.issubset(instruments.columns):
        asset_types = pd.Series(
            instruments["asset_type"].astype(str).str.upper().values,
            index=instruments["symbol"].astype(str).str.upper(),
        )
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
        asset_type = str(asset_types.get(symbol, "")).upper()
        is_common_stock = asset_type == "CS"
        is_beijing = exchange == "BJ" or code.startswith(("4", "8", "920"))
        is_star = exchange == "SH" and code.startswith(("688", "689"))
        is_chinext = exchange == "SZ" and code.startswith(("300", "301"))

        listed_date = listing_dates.get(symbol, pd.NaT)
        if is_common_stock and pd.notna(listed_date):
            listed_date = pd.Timestamp(listed_date).normalize()
            listed_sessions = sum(listed_date <= session <= context.as_of for session in sessions)
            unlimited_sessions = (
                beijing_ipo_unlimited_sessions if is_beijing else ipo_unlimited_sessions
            )
            if 0 < listed_sessions <= unlimited_sessions:
                no_limit_symbols.add(symbol)

        if asset_type == "ETF":
            rate = etf_limit_rate
        elif is_beijing:
            rate = beijing_limit_rate
        elif is_star:
            rate = star_market_limit_rate
        elif is_chinext:
            rate = chinext_limit_rate
        elif is_common_stock and pd.notna(previous_is_st.get(symbol)) and bool(
            previous_is_st.get(symbol)
        ):
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
    fallback_candidates: tuple[str, ...] = (),
):
    """下一交易日开盘尝试一次；默认无候补，买不到的额度留现金且不自动重分配。"""
    # 可提供在信号时已确定的有序候补证券；不要读取未来行情来决定候补名单。
    # 收盘信号不能偷用同一收盘价；next_session_open 明确隔离信号与成交时点。
    return ExecutionPolicy(
        activation="next_session_open",
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
        max_participation_rate=max_participation_rate,
        fallback_candidates=fallback_candidates,
    )
'''


__all__ = ["DEFAULT_STRATEGY_SOURCE"]
