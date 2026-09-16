"""Prepare local RQ snapshots and rerun fixed quality controls through Strategy SDK v1.

Run from the checkout with python -m scripts.research_quality_replication --help.
No provider calls, parameter search, or second backtest engine are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from alphalab import DataEngine, StrategyRepository, run_strategy_backtest
from alphalab.dataio import (
    DataCache,
    LocalParquetFundamentalProvider,
    LocalParquetInstrumentProvider,
    LocalParquetMarketDataProvider,
    build_canonical_fundamentals,
)
from alphalab.strategy import (
    merge_data_requirements,
    replace_registered_function,
    update_parameter_default,
)


def _date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series.astype(str), format="mixed", errors="coerce").dt.normalize()


def _normalize_statement(path: Path, fields: list[str]) -> pd.DataFrame:
    frame = pd.read_parquet(path, columns=["order_book_id", "quarter", "info_date", "if_adjusted", *fields])
    frame["symbol"] = frame.order_book_id.str.replace(".XSHG", ".SH", regex=False).str.replace(
        ".XSHE", ".SZ", regex=False
    ).str.replace(".XBSE", ".BJ", regex=False)
    frame["info_date"] = _date(frame.info_date)
    return frame


def _legacy_roe(income: pd.DataFrame, balance: pd.DataFrame, anchors: pd.DataFrame) -> pd.DataFrame:
    """Preserve the earlier study's latest-equity formula as an explicit control."""
    outputs = []
    for field, data in (("profit_ttm", income), ("book_equity", balance)):
        data = data.copy()
        data["available_date"] = _date(data.info_date) + pd.Timedelta(days=1)
        data["q"] = data.quarter.str[:4].astype(int) * 4 + data.quarter.str[-1].astype(int) - 1
        data = data.sort_values(["symbol", "available_date", "q"])
        events = []
        for symbol, group in data.groupby("symbol", sort=False):
            state = {}
            latest = -1
            for row in group.itertuples():
                if field == "profit_ttm":
                    state[row.q] = row.net_profit_parent_company
                    latest = max(state)
                    q = latest % 4
                    value = state[latest] if q == 3 else state[latest] + state.get(latest - q - 1, np.nan) - state.get(latest - 4, np.nan)
                else:
                    if row.q < latest:
                        continue
                    latest, value = row.q, row.equity_parent_company
                events.append((symbol, row.available_date, value))
        events = pd.DataFrame(events, columns=["symbol", "available_date", field])
        events = events.drop_duplicates(["symbol", "available_date"], keep="last")
        part = pd.merge_asof(anchors.sort_values("date"), events.sort_values("available_date"),
                             by="symbol", left_on="date", right_on="available_date", direction="backward")
        part.loc[(part.date - part.available_date).dt.days.gt(550), field] = np.nan
        outputs.append(part.set_index(["date", "symbol"])[field])
    values = pd.concat(outputs, axis=1)
    values["legacy_roe"] = values.profit_ttm / values.book_equity.where(values.book_equity.gt(0))
    return values[["legacy_roe"]].reset_index()


def prepare(data_root: Path, output: Path, start: str, end: str) -> None:
    """Translate existing caches to an isolated, versioned local experiment input."""
    output.mkdir(parents=True, exist_ok=True)
    lower, upper = pd.Timestamp(start) - pd.Timedelta(days=400), pd.Timestamp(end)
    rq, extra = data_root / "rq", data_root / "sector_rotation/rq"
    paths = [rq / "bars/1d.parquet", extra / "bars.parquet"]
    sources = []
    frames = []
    for path in paths:
        if not path.exists():
            continue
        part = pd.read_parquet(path, filters=[("date", ">=", lower), ("date", "<=", upper)])
        # Legacy caches can store volume in lots. Amount drives this study's
        # liquidity cap; preserve original volume and disclose unit diagnostics.
        ratio = part.amount / (part.volume * part.close).replace(0, np.nan)
        sources.append({"path": str(path), "rows": len(part),
                        "amount_over_close_volume_median": float(ratio.median()) if ratio.notna().any() else None})
        frames.append(part)
    bars = pd.concat(frames, ignore_index=True).drop_duplicates(["date", "symbol"], keep="last")
    bars["date"] = _date(bars.date)
    bars = bars.loc[bars.symbol.str.match(r"^(00\d{4}|30\d{4})\.SZ$|^(60\d{4}|68\d{4})\.SH$|^[489]\d{5}\.BJ$")]
    bars = bars.sort_values(["symbol", "date"]).reset_index(drop=True)
    symbols = set(bars.symbol)
    master = pd.read_parquet(rq / "market_state/instruments.parquet")
    master = master.loc[master.symbol.isin(symbols)].copy()
    master["listed_date"], master["de_listed_date"] = _date(master.listed_date), _date(master.de_listed_date)
    master["snapshot_date"], master["asset_type"] = lower, "CS"
    master.to_parquet(output / "instruments.parquet", index=False)

    for name in ("paused", "is_st"):
        state_parts = []
        for base in (rq / "market_state", extra):
            path = base / f"{name}.parquet"
            if path.exists():
                state = pd.read_parquet(path)
                state.index = pd.to_datetime(state.index)
                state_parts.append(state.loc[state.index.to_series().between(lower, upper), state.columns.intersection(sorted(symbols))])
        state = pd.concat(state_parts)
        state = state.loc[~state.index.duplicated(keep="last")]
        long = state.rename_axis(index="date", columns="symbol").stack(future_stack=True).rename(name)
        bars[name] = long.reindex(pd.MultiIndex.from_frame(bars[["date", "symbol"]])).to_numpy()

    factors = []
    for base in (rq / "market_state", extra):
        path = base / "daily_factors.parquet"
        if path.exists():
            part = pd.read_parquet(path, filters=[("field", "in", ["market_cap", "roe"])])
            part["date"] = _date(part.date)
            factors.append(part.loc[part.date.between(lower, upper), ["date", "symbol", "field", "value"]])
    factors = pd.concat(factors).drop_duplicates(["date", "symbol", "field"], keep="last")
    factor_wide = factors.pivot(index=["date", "symbol"], columns="field", values="value")
    bars["market_cap"] = factor_wide.market_cap.reindex(pd.MultiIndex.from_frame(bars[["date", "symbol"]])).to_numpy()
    bars["vendor_roe"] = factor_wide.roe.reindex(pd.MultiIndex.from_frame(bars[["date", "symbol"]])).to_numpy() / 100
    del factors, factor_wide

    # Keep adjustment factors point-in-time through the ex-date. A fixed scale
    # cancels in returns; these are total-return proxies, not a dividend ledger.
    ex = pd.read_parquet(extra / "ex_factors.parquet")
    ex["date"] = _date(ex.date)
    ex = ex.loc[ex.date.le(upper)].drop_duplicates(["date", "symbol"], keep="last")
    manifest = json.loads((extra / "manifest.json").read_text(encoding="utf-8"))
    if pd.Timestamp(manifest["end"]) < upper:
        raise ValueError("Corporate-action coverage does not reach study end")
    covered = set(manifest["adjustment_symbols"])
    aligned = pd.merge_asof(bars[["date", "symbol"]].sort_values("date"), ex.sort_values("date"),
                             on="date", by="symbol", direction="backward")
    alignment = aligned.set_index(["date", "symbol"]).factor.fillna(1.0)
    adjustment = alignment.reindex(pd.MultiIndex.from_frame(bars[["date", "symbol"]])).to_numpy()
    for field in ("open", "high", "low", "close"):
        bars[f"raw_{field}"] = bars[field]
        bars[field] = bars[field] * adjustment
        bars.loc[~bars.symbol.isin(covered), field] = np.nan
    by_symbol = bars.groupby("symbol", sort=False)
    adv = by_symbol.amount.transform(lambda values: values.rolling(20, min_periods=20).mean())
    observations = by_symbol.close.transform(lambda values: values.rolling(61, min_periods=61).count())
    listing = master.drop_duplicates("symbol").set_index("symbol").listed_date
    age = (bars.date - bars.symbol.map(listing)).dt.days
    bars["eligible"] = (bars.raw_close.ge(2) & adv.ge(20_000_000) & observations.ge(61)
                         & age.ge(183) & bars.market_cap.gt(0) & bars.is_st.eq(False) & bars.paused.eq(False)).astype(float)

    # Historical industry availability reproduces the legacy common universe;
    # an industry entry becomes known only after its effective date.
    industries = pd.read_parquet(rq / "market_state/industry_history.parquet")
    industries = industries.loc[industries.source.eq("citics") & industries.level.eq(1)].copy()
    industries["start_date"], industries["cancel_date"] = _date(industries.start_date), _date(industries.cancel_date)
    industries["start_date"] += pd.Timedelta(days=1)
    matched = pd.merge_asof(bars[["date", "symbol"]].sort_values("date"),
                            industries.sort_values("start_date"), left_on="date", right_on="start_date", by="symbol")
    known = matched.start_date.notna() & matched.date.lt(matched.cancel_date)
    known.index = pd.MultiIndex.from_frame(matched[["date", "symbol"]])
    bars["eligible"] *= known.reindex(pd.MultiIndex.from_frame(bars[["date", "symbol"]])).to_numpy()

    income = _normalize_statement(rq / "income_statement.parquet", ["revenue", "operating_revenue", "cost_of_goods_sold", "gross_profit", "net_profit_parent_company"])
    balance = _normalize_statement(rq / "balance_sheet.parquet", ["equity_parent_company", "total_assets", "total_liabilities", "paid_in_capital"])
    income = income.loc[income.symbol.isin(symbols) & income.info_date.le(upper)]
    balance = balance.loc[balance.symbol.isin(symbols) & balance.info_date.le(upper)]
    fundamentals = build_canonical_fundamentals(income, balance, bars, asof_date=end)
    fundamentals.to_parquet(output / "fundamentals.parquet", index=False)
    sessions = pd.DatetimeIndex(bars.date.unique()).sort_values()
    anchors = pd.Series(sessions, index=sessions).groupby(sessions.to_period("M")).max().to_numpy()
    anchor_rows = bars.loc[bars.date.isin(anchors), ["date", "symbol"]]
    legacy = _legacy_roe(income, balance, anchor_rows)
    bars = bars.merge(legacy, on=["date", "symbol"], how="left")
    # Modern ROE uses the source availability metadata; unchanged old snapshots
    # are not silently carried past the 550-day age limit used in the earlier study.
    for field in ("roe", "roe_latest_equity", "roa", "leverage"):
        part = pd.merge_asof(anchor_rows.sort_values("date"), fundamentals[["available_date", "symbol", "quarter", field]].sort_values(["available_date", "quarter"]),
                             left_on="date", right_on="available_date", by="symbol", direction="backward")
        part.loc[(part.date - part.available_date).dt.days.gt(550), field] = np.nan
        bars = bars.merge(part[["date", "symbol", field]], on=["date", "symbol"], how="left")

    membership = pd.read_parquet(rq / "market_state/index_components.parquet")
    membership["date"] = _date(membership.date)
    membership = membership.loc[membership.index_symbol.eq("000300.SH") & membership.date.le(upper)]
    index_frames = []
    for anchor in pd.DatetimeIndex(anchors):
        available = membership.loc[membership.date.le(anchor)]
        if available.empty:
            continue
        stamp = available.date.max()
        members = set(available.loc[available.date.eq(stamp), "symbol"])
        rows = anchor_rows.loc[anchor_rows.date.eq(anchor)].copy()
        rows["csi300"] = rows.symbol.isin(members).astype(float)
        rows["membership_age_days"] = (anchor - stamp).days
        index_frames.append(rows)
    bars = bars.merge(pd.concat(index_frames), on=["date", "symbol"], how="left")
    bars.to_parquet(output / "bars.parquet", index=False)
    snapshot_manifest = {
        "source_root": str(data_root), "requested_start": start, "requested_end": end,
        "rows": len(bars), "symbols": bars.symbol.nunique(), "sessions": len(sessions),
        "first_session": str(sessions.min().date()), "last_session": str(sessions.max().date()),
        "sources": sources, "original_income_rows": int(income.if_adjusted.eq(0).sum()),
        "adjusted_only_income_rows_excluded_from_modern": int(income.if_adjusted.ne(0).sum()),
        "execution_model": "SDK v1 normalized fractional weights; no round-lot or minimum-commission model",
        "price_basis": "raw OHLC times historical cumulative ex-factor; total-return proxy",
        "limitations": ["Financial revision archive is incomplete", "Index membership snapshots are monthly",
                         "Session calendar inferred from observed bars", "Source volume unit may differ across files; liquidity uses CNY amount"],
        "files": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in ("bars.parquet", "fundamentals.parquet", "instruments.parquet")},
    }
    (output / "manifest.json").write_text(json.dumps(snapshot_manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(snapshot_manifest, ensure_ascii=False, indent=2), flush=True)


VARIANTS = {
    "legacy_roe_all": ("legacy_roe", False, False),
    "latest_equity_all": ("roe_latest_equity", False, False),
    "average_equity_all": ("roe", False, False),
    "average_equity_csi300": ("roe", True, False),
    "average_equity_csi300_cap": ("roe", True, True),
    "vendor_roe_csi300": ("vendor_roe", True, False),
    "quality_composite_csi300": ("composite", True, False),
}


class SnapshotResearch:
    """Expose stored market state through the research-provider contract."""

    def __init__(self, market: LocalParquetMarketDataProvider):
        self.market = market

    def get_market_state(self, symbols, start, end, fields=None):
        requested = fields or ["paused", "is_st"]
        physical = list(dict.fromkeys("paused" if name == "is_suspended" else name for name in requested))
        frame = self.market.get_bars(symbols, start, end, fields=physical)
        if "is_suspended" in requested:
            frame["is_suspended"] = frame["paused"]
        return frame[["date", "symbol", *requested]]

    def get_daily_factors(self, symbols, fields, start, end):
        return pd.DataFrame(columns=["date", "symbol", "field", "value"])

    def get_index_components(self, index_symbols, start, end):
        return pd.DataFrame(columns=["date", "index_symbol", "symbol"])


class ScopedInstruments(LocalParquetInstrumentProvider):
    """Limit I/O to the union of historical members; dated gates still select the universe."""

    def __init__(self, snapshot: Path, symbols: set[str] | None):
        super().__init__(snapshot)
        self.symbols = symbols

    def get_instrument_master(self):
        frame = super().get_instrument_master()
        return frame if self.symbols is None else frame.loc[frame.symbol.isin(self.symbols)].copy()

    def get_instruments(self, asof_date=None):
        frame = super().get_instruments(asof_date)
        return frame if self.symbols is None else frame.loc[frame.symbol.isin(self.symbols)].copy()


def run(snapshot: Path, output: Path, start: str, end: str, variants: list[str]) -> None:
    """Use normal project saves and the current event engine for every control."""
    output.mkdir(parents=True, exist_ok=True)
    data = DataEngine(DataCache(output / "cache"))
    market = LocalParquetMarketDataProvider(snapshot)
    data.register_market("local", market)
    data.register_research("local", SnapshotResearch(market))
    data.register_instrument("local", LocalParquetInstrumentProvider(snapshot))
    data.register_fundamental("local", LocalParquetFundamentalProvider(snapshot))
    members = pd.read_parquet(snapshot / "bars.parquet", columns=["symbol", "csi300"])
    index_union = set(members.loc[members.csi300.eq(1), "symbol"])
    del members
    repo = StrategyRepository(output / "projects.db")
    # Copying the canonical project retains its authored execution-data fill;
    # controlled replacements are persisted through the normal project APIs.
    try:
        for name in variants:
            field, csi300, cap_weighted = VARIANTS[name]
            data.register_instrument("local", ScopedInstruments(snapshot, index_union if csi300 else None), default=True)
            print(f"RUN {name}: {start} -> {end}", flush=True)
            project = repo.get_project(name) or repo.clone_project("sdk-v1-default", name, name=name)
            source = repo.get_project("sdk-v1-default")["draft_source"]
            fields = ["open", "high", "low", "close", "volume", "amount", "raw_open", "raw_close", "is_st", "paused",
                      "eligible", "legacy_roe", "roe", "roe_latest_equity", "roa", "leverage", "market_cap", "csi300", "vendor_roe"]
            source, _ = merge_data_requirements(source, {"bars": fields, "instruments": ["asset_type"]})
            replacements = {
                "research_universe": '''@universe(id="research_universe")
def research_universe(context):
    """Keep the full point-in-time instrument scope; selection gates run at signal close."""
    # No future membership or future outcomes determine the eligible scope.
    return UniverseResult(symbols=context.universe)
''',
                "momentum_20d": f'''@factor(id="quality")
def quality(context):
    """Fixed quality control; returns an as-of cross section with missing inputs retained."""
    # Inputs were joined to their historical availability before the snapshot was frozen.
    if {field!r} == "composite":
        import pandas as pd
        values = pd.concat([context.history("roe", window=1).iloc[-1], context.history("roa", window=1).iloc[-1],
                            -context.history("leverage", window=1).iloc[-1]], axis=1)
        complete = values.notna().all(axis=1) & context.history("eligible", window=1).iloc[-1].eq(1)
        if {csi300!r}:
            complete &= context.history("csi300", window=1).iloc[-1].eq(1)
        return values.where(complete).rank(pct=True).mean(axis=1).where(complete)
    return context.history({field!r}, window=1).iloc[-1]
''',
                "monthly_momentum": f'''@signal(id="quality_monthly", schedule=Monthly.last_trading_day(at="close"))
def quality_monthly(context, state, *, top_n: int = 50):
    """Select a fixed top-50 quality cross section at month-end; next-open execution."""
    scores = context.factor("quality")
    eligible = context.history("eligible", window=1).iloc[-1].eq(1)
    if {csi300!r}:
        eligible &= context.history("csi300", window=1).iloc[-1].eq(1)
    scores = scores.where(eligible).dropna().sort_values(ascending=False, kind="stable")
    # Keep target breadth explicit; missing data never grants a favourable rank.
    selected = list(scores.head(top_n).index)
    return SignalResult(selected=selected, scores=scores, state=state)
''',
                "equal_weight": f'''@portfolio(id="quality_weights")
def quality_weights(context, signal, state):
    """Compare equal versus total-market-cap weights on the same selected names."""
    # Total cap is an explicit proxy; it is not the index provider's free-float weights.
    if not signal.selected:
        return PortfolioDecision(target_weights={{}}, state=state)
    if {cap_weighted!r}:
        cap = context.history("market_cap", window=1).iloc[-1].reindex(signal.selected)
        weights = (cap / cap.sum()).to_dict()
    else:
        weights = {{symbol: 1.0 / 50 for symbol in signal.selected}}
    return PortfolioDecision(target_weights=weights, state=state)
''',
            }
            for entrypoint, replacement in replacements.items():
                source, _ = replace_registered_function(source, entrypoint_id=entrypoint, function_source=replacement)
            # Same proportional fees for all controls. The SDK currently does not
            # implement the old cash account's lots, minimum fee or stamp-duty ledger.
            for parameter, value in {"commission_rate": 0.0003, "slippage_rate": 0.001,
                                     "max_participation_rate": 0.01}.items():
                source, _ = update_parameter_default(source, entrypoint_id="next_open", parameter=parameter, value=value)
            project = repo.update_draft(name, source, expected_source_sha256=project["draft_source_sha256"])
            result = run_strategy_backtest(repo, name, start, end, data, execution_data_policy="strict")
            folder = output / name
            folder.mkdir(exist_ok=True)
            result.returns.to_csv(folder / "returns.csv")
            result.weights.to_parquet(folder / "weights.parquet")
            (folder / "executions.json").write_text(json.dumps(result.executions, default=str), encoding="utf-8")
            (folder / "diagnostics.json").write_text(json.dumps(result.diagnostics, default=str), encoding="utf-8")
            (folder / "strategy.py").write_text(source, encoding="utf-8")
            metadata = {
                "variant": name, "start": start, "end": end,
                "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "snapshot_manifest_sha256": hashlib.sha256((snapshot / "manifest.json").read_bytes()).hexdigest(),
                "configuration": {"field": field, "csi300": csi300, "total_cap_weights": cap_weighted},
                "execution_data_policy": "strict", "commission_rate": 0.0003,
                "slippage_rate": 0.001, "max_participation_rate": 0.01,
                "model": "SDK v1 fractional-weight portfolio; no round lots, minimum fees or stamp-duty ledger",
            }
            (folder / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            annual = (1 + result.returns).groupby(result.returns.index.year).prod() - 1
            print(f"RESULT {name}: {annual.to_dict()}; execution_reliable={result.diagnostics.get('execution_reliable')}", flush=True)
    finally:
        repo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run"))
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--start", default="2018-12-28")
    parser.add_argument("--end", default="2020-12-31")
    parser.add_argument("--variants", nargs="+", choices=list(VARIANTS), default=list(VARIANTS))
    args = parser.parse_args()
    if args.mode == "prepare":
        if args.data_root is None:
            parser.error("prepare requires --data-root")
        prepare(args.data_root, args.snapshot, args.start, args.end)
    else:
        if args.output is None:
            parser.error("run requires --output")
        run(args.snapshot, args.output, args.start, args.end, args.variants)


if __name__ == "__main__":
    main()
