"""Common-date comparisons and index exposures over canonical saved runs."""

import numpy as np
import pandas as pd

from alphalab.analytics import equal_weight_benchmark, factor_attribution
from alphalab.dataio.symbols import to_rq_symbol
from apps.api.services import options_strategy_service, result_service
from apps.api.services.data_service import _engine

INDEX_GROUPS = {
    "510050.XSHG": ("上证50", "SSE 50"),
    "510300.XSHG": ("沪深300", "CSI 300"),
    "159919.XSHE": ("沪深300", "CSI 300"),
    "510500.XSHG": ("中证500", "CSI 500"),
    "159922.XSHE": ("中证500", "CSI 500"),
    "159915.XSHE": ("创业板", "ChiNext"),
    "159901.XSHE": ("深证100", "SZSE 100"),
    "588000.XSHG": ("科创50", "STAR 50"),
    "588080.XSHG": ("科创50", "STAR 50"),
}


def compare(ids=None, *, start=None, end=None):
    cards = options_strategy_service.strategies()["strategies"]
    if ids:
        if len(ids) > 12:
            raise ValueError("Select at most 12 strategy runs")
        cards = [options_strategy_service.strategy_result(i) for i in dict.fromkeys(ids)]
    if not cards:
        return {"rows": [], "period": None, "curve": []}
    records = [result_service.get_backtest(c["backtest_id"]) for c in cards]
    series = {
        c["backtest_id"]: pd.Series(
            {pd.Timestamp(r["date"]): r["value"] for r in record["returns"]}, dtype=float
        )
        for c, record in zip(cards, records)
    }
    joined = pd.DataFrame(series).replace([np.inf, -np.inf], np.nan).dropna()
    if start is not None:
        joined = joined.loc[joined.index >= pd.Timestamp(start)]
    if end is not None:
        joined = joined.loc[joined.index <= pd.Timestamp(end)]
    if joined.empty:
        raise ValueError("Saved runs have no common return dates")
    symbols = sorted({s for c in cards for s in c["rule"]["symbols"]})
    representatives = {}
    for symbol in symbols:
        representatives.setdefault(
            INDEX_GROUPS.get(to_rq_symbol(symbol), (symbol, symbol))[1], symbol
        )
    engine = _engine("runtime")

    def benchmark(names):
        return equal_weight_benchmark(
            engine,
            names,
            str((joined.index.min() - pd.Timedelta(days=40)).date()),
            str(joined.index.max().date()),
            frequency="daily",
            execution_price="close",
            label_period_end=True,
        )

    pool = benchmark(symbols)
    balanced = benchmark(list(representatives.values()))
    joined = (
        joined.join(pool.rename("pool_equal_weight"))
        .join(balanced.rename("index_equal_weight"))
        .dropna()
    )
    if joined.empty:
        raise ValueError("Benchmark coverage is insufficient on the shared dates")
    factors = pd.DataFrame({"MKT": joined.pool_equal_weight, "rf": 0.0})
    output = []
    for card, record in zip(cards, records):
        values = joined[card["backtest_id"]]
        total = float((1 + values).prod() - 1)
        attribution = factor_attribution(values, factors)
        weights = pd.DataFrame(record.get("weights", []))
        exposures = []
        if not weights.empty:
            weights = weights.loc[pd.to_datetime(weights.date).isin(joined.index)].copy()
            weights["index"] = weights.symbol.map(
                lambda s: INDEX_GROUPS.get(to_rq_symbol(s), (s, s))[1]
            )
            weights["zh"] = weights.symbol.map(
                lambda s: INDEX_GROUPS.get(to_rq_symbol(s), (s, s))[0]
            )
            if not weights.empty:
                daily_weights = weights.pivot_table(
                    index="date", columns="index", values="weight", aggfunc="sum", fill_value=0
                )
                daily_weights.index = pd.to_datetime(daily_weights.index)
                grouped = daily_weights.reindex(joined.index, fill_value=0).mean()
                exposures = [
                    {
                        "name_en": name,
                        "name_zh": weights.loc[weights["index"].eq(name), "zh"].iloc[0],
                        "mean_weight": float(weight),
                    }
                    for name, weight in grouped.items()
                ]
        output.append(
            {
                "backtest_id": card["backtest_id"],
                "name_zh": card["name_zh"],
                "name_en": card["name_en"],
                "total_return": total,
                "execution_reliable": card.get("execution_reliable"),
                "research_valid": card.get("research_valid"),
                "execution_warnings": card.get("warnings", []),
                "pool_relative_return": float(
                    (1 + total) / (1 + joined.pool_equal_weight).prod() - 1
                ),
                "index_relative_return": float(
                    (1 + total) / (1 + joined.index_equal_weight).prod() - 1
                ),
                "market_proxy_attribution": attribution["capm"],
                "attribution_warnings": attribution["warnings"],
                "index_exposures": exposures,
            }
        )
    nav = (1 + joined).cumprod()
    return {
        "period": {
            "start": str(joined.index.min().date()),
            "end": str(joined.index.max().date()),
            "sessions": len(joined),
        },
        "rows": output,
        "benchmarks": {
            "pool_equal_weight": float((1 + joined.pool_equal_weight).prod() - 1),
            "index_equal_weight": float((1 + joined.index_equal_weight).prod() - 1),
            "pool_symbols": symbols,
            "index_representatives": list(representatives.values()),
        },
        "curve": [
            {"date": str(d.date()), **{k: float(v) for k, v in row.items()}}
            for d, row in nav.iterrows()
        ],
        "assumptions": "Common valid dates only. Canonical daily close-to-close benchmark helper, labeled at period end; benchmarks gross of costs, strategies net. CAPM uses ETF-pool proxy and zero risk-free rate, monthly HAC inference; descriptive attribution, not proof of alpha. Multiple testing remains uncorrected.",
    }
