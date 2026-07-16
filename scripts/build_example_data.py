"""Build the compact, real-data AlphaLab example bundle.

The source repository is read-only. This script normalizes selected QMT and RQ
parquets into the vendor-neutral contracts consumed by the barebone framework.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from alphalab import ResultStore, SignalEngine, StrategyConfig, create_default_engine, run_backtest
from alphalab.analytics import PerformanceMetrics
from alphalab.strategies import list_strategy_files

DEFAULT_SOURCE = Path(r"C:\Users\LYT\Documents\GitHub\quant-framework-factors")
DEFAULT_TARGET = Path(__file__).resolve().parents[1]
PRICE_FIELDS = ("open", "high", "low", "close")
FUNDAMENTAL_FIELDS = (
    "ep",
    "bp",
    "roe",
    "gross_margin",
    "leverage",
    "profit_growth",
    "revenue_growth",
)


def _qmt_symbol(value: str) -> str:
    text = str(value).upper()
    return text.replace(".XSHE", ".SZ").replace(".XSHG", ".SH")


def _rq_symbol(value: str) -> str:
    text = str(value).upper()
    return text.replace(".SZ", ".XSHE").replace(".SH", ".XSHG")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _first_release(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if "if_adjusted" in frame:
        frame = frame.loc[frame["if_adjusted"].fillna(0).eq(0)]
    frame["info_date"] = pd.to_datetime(frame["info_date"], errors="coerce")
    return (
        frame.dropna(subset=["order_book_id", "quarter", "info_date"])
        .sort_values(["order_book_id", "quarter", "info_date"])
        .drop_duplicates(["order_book_id", "quarter"], keep="first")
    )


def _select_universe(source_data: Path, sample_start: pd.Timestamp, count: int) -> tuple[list[str], dict]:
    qmt_dir = source_data / "qmt"
    rq_dir = source_data / "rq"
    stock_info = pd.read_parquet(qmt_dir / "stock_info.parquet")
    stock_info["stock"] = stock_info["stock"].map(_qmt_symbol)
    stock_info["listing_date"] = pd.to_datetime(stock_info["listing_date"], format="%Y%m%d", errors="coerce")

    rq_symbols = set(
        pd.read_parquet(rq_dir / "income_statement.parquet", columns=["order_book_id"])
        ["order_book_id"]
        .dropna()
        .map(_qmt_symbol)
    )
    eligible = stock_info.loc[
        ~stock_info["is_st"].fillna(False)
        & ~stock_info["is_financial"].fillna(False)
        & stock_info["listing_date"].le(sample_start - pd.Timedelta(days=365))
        & stock_info["stock"].isin(rq_symbols)
    ].copy()

    training_end = sample_start - pd.Timedelta(days=1)
    training_start = training_end - pd.DateOffset(years=1)
    amount = pd.read_parquet(qmt_dir / "amount.parquet", columns=eligible["stock"].tolist())
    amount.index = pd.to_datetime(amount.index)
    training = amount.loc[training_start:training_end].replace(0, np.nan)
    ranking = pd.DataFrame(
        {
            "median_amount": training.median(),
            "coverage": training.notna().mean(),
        }
    )
    ranking = ranking.loc[ranking["coverage"].ge(0.90)].sort_values(
        ["median_amount", "coverage"], ascending=False
    )
    symbols = ranking.head(count).index.astype(str).tolist()
    if len(symbols) != count:
        raise RuntimeError(f"Expected {count} eligible symbols, found {len(symbols)}")
    return symbols, {
        "method": "top median daily amount before sample start",
        "training_start": training_start.strftime("%Y-%m-%d"),
        "training_end": training_end.strftime("%Y-%m-%d"),
        "minimum_training_coverage": 0.90,
        "excluded": ["ST", "financial", "listed less than one year before sample"],
    }


def _load_adjustment_factor(qmt_dir: Path, index: pd.DatetimeIndex, symbols: list[str]) -> pd.DataFrame:
    dividends = pd.read_parquet(qmt_dir / "divid_factors.parquet", columns=["date", "stock", "dr"])
    dividends["date"] = pd.to_datetime(dividends["date"], errors="coerce")
    dividends["stock"] = dividends["stock"].map(_qmt_symbol)
    dividends = dividends.loc[
        dividends["stock"].isin(symbols) & dividends["dr"].gt(0)
    ]
    event_factor = dividends.pivot_table(index="date", columns="stock", values="dr", aggfunc="prod")
    return event_factor.reindex(index=index, columns=symbols).fillna(1.0).cumprod()


def _build_market(
    source_data: Path,
    target_data: Path,
    symbols: list[str],
    sample_start: pd.Timestamp,
    cutoff: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    qmt_dir = source_data / "qmt"
    raw_close = pd.read_parquet(qmt_dir / "close.parquet", columns=symbols)
    raw_close.index = pd.to_datetime(raw_close.index)
    raw_close = raw_close.sort_index().loc[:cutoff]
    factor = _load_adjustment_factor(qmt_dir, raw_close.index, symbols)

    wide: dict[str, pd.DataFrame] = {}
    for field in (*PRICE_FIELDS, "volume", "amount"):
        frame = pd.read_parquet(qmt_dir / f"{field}.parquet", columns=symbols)
        frame.index = pd.to_datetime(frame.index)
        frame = frame.sort_index().reindex(raw_close.index)
        if field in PRICE_FIELDS:
            frame = frame * factor
        wide[field] = frame.loc[sample_start:cutoff]

    long_fields = []
    for field, frame in wide.items():
        series = frame.stack(future_stack=True).rename(field)
        series.index.names = ["date", "symbol"]
        long_fields.append(series)
    bars = pd.concat(long_fields, axis=1).reset_index()
    bars = bars.dropna(subset=["close"])
    bars = bars.loc[bars["close"].gt(0) & bars["high"].ge(bars["low"])]
    for field in PRICE_FIELDS:
        bars[field] = pd.to_numeric(bars[field], errors="coerce").astype("float32")
    bars["volume"] = pd.to_numeric(bars["volume"], errors="coerce").fillna(0).astype("float64")
    bars["amount"] = pd.to_numeric(bars["amount"], errors="coerce").fillna(0).astype("float64")
    bars = bars.sort_values(["date", "symbol"]).reset_index(drop=True)

    market_dir = target_data / "market"
    market_dir.mkdir(parents=True, exist_ok=True)
    bars.to_parquet(market_dir / "bars.parquet", index=False, compression="zstd")
    adjusted_close = raw_close * factor
    return bars, raw_close, adjusted_close


def _quarter_parts(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["quarter"] = result["quarter"].astype(str).str.lower()
    result["year"] = result["quarter"].str[:4].astype(int)
    result["quarter_no"] = result["quarter"].str[-1].astype(int)
    return result


def _single_quarter(frame: pd.DataFrame, column: str) -> pd.Series:
    grouped = frame.groupby(["symbol", "year"], sort=False)[column]
    single = grouped.diff()
    return single.where(frame["quarter_no"].ne(1), frame[column])


def _price_on_or_before(raw_close: pd.DataFrame, rows: pd.DataFrame) -> pd.Series:
    output = pd.Series(np.nan, index=rows.index, dtype=float)
    for symbol, indices in rows.groupby("symbol").groups.items():
        if symbol not in raw_close:
            continue
        series = raw_close[symbol].dropna().sort_index()
        if series.empty:
            continue
        dates = pd.DatetimeIndex(rows.loc[indices, "available_date"])
        positions = series.index.searchsorted(dates, side="right") - 1
        valid = positions >= 0
        values = np.full(len(indices), np.nan)
        values[valid] = series.iloc[positions[valid]].to_numpy(dtype=float)
        output.loc[indices] = values
    return output


def _build_fundamentals(
    source_data: Path,
    target_data: Path,
    symbols: list[str],
    raw_close: pd.DataFrame,
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    rq_dir = source_data / "rq"
    rq_symbols = {_rq_symbol(symbol) for symbol in symbols}
    income_columns = [
        "order_book_id", "quarter", "info_date", "if_adjusted", "revenue",
        "operating_revenue", "cost_of_goods_sold", "gross_profit", "net_profit_parent_company",
    ]
    balance_columns = [
        "order_book_id", "quarter", "info_date", "if_adjusted", "total_assets",
        "total_liabilities", "equity_parent_company", "paid_in_capital",
    ]
    income = pd.read_parquet(rq_dir / "income_statement.parquet", columns=income_columns)
    balance = pd.read_parquet(rq_dir / "balance_sheet.parquet", columns=balance_columns)
    income = _first_release(income.loc[income["order_book_id"].isin(rq_symbols)])
    balance = _first_release(balance.loc[balance["order_book_id"].isin(rq_symbols)])
    income = income.rename(columns={"info_date": "income_date"})
    balance = balance.rename(columns={"info_date": "balance_date"})
    keep_income = [column for column in income.columns if column != "if_adjusted"]
    keep_balance = [column for column in balance.columns if column != "if_adjusted"]
    merged = income[keep_income].merge(
        balance[keep_balance], on=["order_book_id", "quarter"], how="inner", suffixes=("", "_bs")
    )
    merged["symbol"] = merged["order_book_id"].map(_qmt_symbol)
    merged["available_date"] = merged[["income_date", "balance_date"]].max(axis=1)
    merged = merged.loc[merged["available_date"].le(cutoff)].sort_values(["symbol", "quarter"])
    merged = _quarter_parts(merged)

    merged["revenue_base"] = merged["revenue"].combine_first(merged["operating_revenue"])
    merged["gross_profit_base"] = merged["gross_profit"].combine_first(
        merged["revenue_base"] - merged["cost_of_goods_sold"]
    )
    for source, target in [
        ("revenue_base", "revenue_single"),
        ("gross_profit_base", "gross_profit_single"),
        ("net_profit_parent_company", "profit_single"),
    ]:
        merged[target] = _single_quarter(merged, source)
        merged[target.replace("single", "ttm")] = merged.groupby("symbol", sort=False)[target].transform(
            lambda values: values.rolling(4, min_periods=4).sum()
        )

    grouped = merged.groupby("symbol", sort=False)
    merged["shares"] = merged["paid_in_capital"]
    merged["price_at_available"] = _price_on_or_before(raw_close, merged)
    merged["market_cap"] = merged["price_at_available"] * merged["shares"]
    merged["ep"] = merged["profit_ttm"] / merged["market_cap"]
    merged["bp"] = merged["equity_parent_company"] / merged["market_cap"]
    average_equity = (merged["equity_parent_company"] + grouped["equity_parent_company"].shift(4)) / 2
    merged["roe"] = merged["profit_ttm"] / average_equity
    merged["gross_margin"] = merged["gross_profit_ttm"] / merged["revenue_ttm"]
    merged["leverage"] = merged["total_liabilities"] / merged["total_assets"]
    merged["profit_growth"] = grouped["profit_ttm"].pct_change(4, fill_method=None)
    merged["revenue_growth"] = grouped["revenue_ttm"].pct_change(4, fill_method=None)

    columns = ["quarter", "available_date", "symbol", "shares", "market_cap", *FUNDAMENTAL_FIELDS]
    output = merged[columns].replace([np.inf, -np.inf], np.nan)
    output = output.dropna(subset=list(FUNDAMENTAL_FIELDS), how="all")
    output = output.sort_values(["available_date", "symbol", "quarter"]).reset_index(drop=True)
    fundamental_dir = target_data / "fundamentals"
    fundamental_dir.mkdir(parents=True, exist_ok=True)
    output.to_parquet(fundamental_dir / "fundamentals.parquet", index=False, compression="zstd")
    return output


def _latest_fundamentals(fundamentals: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    return (
        fundamentals.loc[fundamentals["available_date"].le(asof)]
        .sort_values(["symbol", "available_date", "quarter"])
        .groupby("symbol", as_index=False)
        .tail(1)
        .set_index("symbol")
    )


def _spread(values: pd.Series, returns: pd.Series, high_minus_low: bool = True) -> float:
    aligned = pd.concat([values.rename("value"), returns.rename("return")], axis=1).dropna()
    if len(aligned) < 30:
        return np.nan
    low = aligned.nsmallest(max(10, int(len(aligned) * 0.3)), "value")["return"].mean()
    high = aligned.nlargest(max(10, int(len(aligned) * 0.3)), "value")["return"].mean()
    return float(high - low if high_minus_low else low - high)


def _build_factor_returns(
    source_data: Path,
    target_data: Path,
    adjusted_close: pd.DataFrame,
    raw_close: pd.DataFrame,
    fundamentals: pd.DataFrame,
    sample_start: pd.Timestamp,
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    adjusted_monthly = adjusted_close.resample("ME").last().loc[:cutoff]
    raw_monthly = raw_close.resample("ME").last().loc[:cutoff]
    monthly_returns = adjusted_monthly.pct_change(fill_method=None)
    rows: list[dict] = []
    dates = monthly_returns.index[monthly_returns.index >= sample_start]
    for date in dates:
        position = monthly_returns.index.get_loc(date)
        if position < 1:
            continue
        signal_date = monthly_returns.index[position - 1]
        returns = monthly_returns.loc[date].replace([np.inf, -np.inf], np.nan)
        latest = _latest_fundamentals(fundamentals, signal_date)
        if latest.empty:
            continue
        shares = latest["shares"].reindex(raw_monthly.columns)
        market_cap = raw_monthly.loc[signal_date] * shares
        momentum = pd.Series(dtype=float)
        if position >= 12:
            momentum = adjusted_monthly.iloc[position - 1] / adjusted_monthly.iloc[position - 12] - 1
        rows.append(
            {
                "date": date,
                "MKT": float(returns.mean()),
                "SMB": _spread(market_cap, returns, high_minus_low=False),
                "HML": _spread(latest["bp"], returns),
                "MOM": _spread(momentum, returns),
                "RMW": _spread(latest["roe"], returns),
            }
        )
    factors = pd.DataFrame(rows).set_index("date").sort_index()

    rf_path = source_data / "csmar" / "TRD_Nrrate.parquet"
    rf = pd.read_parquet(rf_path, columns=["Clsdt", "Nrrmtdt"])
    rf["Clsdt"] = pd.to_datetime(rf["Clsdt"], format="mixed", errors="coerce")
    rf["Nrrmtdt"] = pd.to_numeric(rf["Nrrmtdt"], errors="coerce") / 100.0
    rf = rf.dropna().set_index("Clsdt")["Nrrmtdt"].resample("ME").last()
    factors["rf"] = rf.reindex(factors.index, method="ffill").fillna(0.0)
    factor_dir = target_data / "factors"
    factor_dir.mkdir(parents=True, exist_ok=True)
    factors.to_parquet(factor_dir / "factor_returns.parquet", compression="zstd")
    return factors


def _insert_demo_orders(db_path: Path, signal_id: str, targets: dict[str, float], prices: dict[str, float]) -> None:
    with sqlite3.connect(db_path) as connection:
        rows = []
        capital = 1_000_000.0
        for index, (symbol, weight) in enumerate(targets.items()):
            price = float(prices[symbol])
            quantity = float(np.floor(capital * weight / price / 100) * 100)
            if quantity <= 0:
                continue
            rows.append(
                (
                    f"demo-order-{index + 1:02d}", signal_id, symbol, "buy", quantity,
                    price, price, quantity, quantity * price * 0.0003, "filled", "paper",
                )
            )
        connection.executemany(
            """INSERT INTO orders
               (id, signal_id, symbol, action, quantity, price, fill_price, fill_quantity,
                commission, status, broker)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        connection.commit()


def _seed_database(target_root: Path, cutoff: pd.Timestamp, sample_start: pd.Timestamp) -> dict:
    db_path = target_root / "data" / "app" / "alphalab.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-shm", "-wal"):
        (Path(str(db_path) + suffix)).unlink(missing_ok=True)

    store = ResultStore(db_path)
    engine = create_default_engine(target_root / "data")
    summaries: dict[str, dict] = {}
    latest_targets: dict[str, float] = {}
    latest_signal_id = ""
    try:
        for strategy_path in list_strategy_files():
            cfg = StrategyConfig.from_yaml(strategy_path)
            relative_path = strategy_path.relative_to(target_root).as_posix()
            store.register_strategy(cfg.name, relative_path, cfg.description)
            returns, weights = run_backtest(
                cfg,
                sample_start.strftime("%Y-%m-%d"),
                cutoff.strftime("%Y-%m-%d"),
                data_engine=engine,
            )
            metrics = PerformanceMetrics.summarize(returns)
            store.save_backtest(
                cfg.to_yaml(), returns, metrics, strategy_id=cfg.name, weights=weights,
                start_date=sample_start.strftime("%Y-%m-%d"), end_date=cutoff.strftime("%Y-%m-%d"),
                tags=["bundled-example", "real-data"], notes="Bundled real-data demonstration run.",
            )
            targets = SignalEngine(engine).generate_targets(cfg, cutoff.strftime("%Y-%m-%d"))
            signal_id = store.save_signal(cfg.name, cutoff.strftime("%Y-%m-%d"), targets, status="paper")
            summaries[cfg.name] = {"metrics": metrics, "target_count": len(targets)}
            if cfg.name == "balanced":
                latest_targets = targets
                latest_signal_id = signal_id
    finally:
        store.close()

    if latest_targets:
        prices = (
            pd.read_parquet(target_root / "data" / "market" / "bars.parquet")
            .sort_values("date")
            .groupby("symbol")["close"]
            .last()
            .reindex(latest_targets)
            .dropna()
            .to_dict()
        )
        _insert_demo_orders(db_path, latest_signal_id, latest_targets, prices)
    return summaries


def _validate_data(bars: pd.DataFrame, fundamentals: pd.DataFrame, factors: pd.DataFrame, count: int) -> None:
    if bars["symbol"].nunique() != count:
        raise RuntimeError("Market data symbol count does not match requested universe")
    if bars.duplicated(["date", "symbol"]).any():
        raise RuntimeError("Market data contains duplicate date/symbol rows")
    invalid_ohlc = bars["high"].lt(bars[["open", "close", "low"]].max(axis=1)) | bars["low"].gt(
        bars[["open", "close", "high"]].min(axis=1)
    )
    if invalid_ohlc.any():
        raise RuntimeError(f"Market data contains {int(invalid_ohlc.sum())} invalid OHLC rows")
    if fundamentals["available_date"].isna().any():
        raise RuntimeError("Fundamental data contains missing available_date values")
    if not set(FUNDAMENTAL_FIELDS).issubset(fundamentals.columns):
        raise RuntimeError("Fundamental data is missing required factor fields")
    if factors.empty or not {"MKT", "SMB", "HML", "MOM", "RMW", "rf"}.issubset(factors.columns):
        raise RuntimeError("Factor return data is incomplete")


def build(source_root: Path, target_root: Path, symbol_count: int = 300) -> dict:
    source_data = source_root / "data"
    target_data = target_root / "data"
    close_path = source_data / "qmt" / "close.parquet"
    close_probe = pd.read_parquet(close_path, columns=["000001.SZ"])
    cutoff = pd.Timestamp(close_probe.index.max()).normalize()
    sample_start = cutoff - pd.DateOffset(years=5) + pd.Timedelta(days=1)

    symbols, universe_method = _select_universe(source_data, sample_start, symbol_count)
    bars, raw_close, adjusted_close = _build_market(
        source_data, target_data, symbols, sample_start, cutoff
    )
    fundamentals = _build_fundamentals(
        source_data, target_data, symbols, raw_close, cutoff
    )
    factors = _build_factor_returns(
        source_data, target_data, adjusted_close, raw_close, fundamentals, sample_start, cutoff
    )
    _validate_data(bars, fundamentals, factors, symbol_count)
    backtests = _seed_database(target_root, cutoff, sample_start)

    files = [
        target_data / "market" / "bars.parquet",
        target_data / "fundamentals" / "fundamentals.parquet",
        target_data / "factors" / "factor_returns.parquet",
        target_data / "app" / "alphalab.db",
    ]
    manifest = {
        "bundle": "alphalab-real-example",
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "source_root": "quant-framework-factors local caches",
        "source_types": ["QMT historical cache", "RQ financial statements", "CSMAR risk-free rate"],
        "realtime": "not_configured",
        "sample_start": sample_start.strftime("%Y-%m-%d"),
        "cutoff_date": cutoff.strftime("%Y-%m-%d"),
        "symbol_count": symbol_count,
        "symbols": symbols,
        "universe": universe_method,
        "price_adjustment": "QMT dr cumulative adjustment applied to OHLC; volume and amount remain raw",
        "fundamental_timing": "first release only; available_date must be on or before decision date",
        "factor_returns": "monthly decimal returns; MKT/SMB/HML/MOM/RMW are illustrative sample-universe spreads",
        "caveat": "Development demonstration data, not an unbiased investable benchmark.",
        "rows": {
            "market": len(bars),
            "fundamentals": len(fundamentals),
            "factor_returns": len(factors),
        },
        "seeded_strategies": backtests,
        "files": {
            path.relative_to(target_root).as_posix(): {
                "bytes": path.stat().st_size,
                ("seed_sha256" if path.suffix == ".db" else "sha256"): _sha256(path),
            }
            for path in files
        },
    }
    manifest_path = target_data / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target-root", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--symbols", type=int, default=300)
    args = parser.parse_args()
    manifest = build(args.source_root.resolve(), args.target_root.resolve(), args.symbols)
    print(json.dumps({
        "sample_start": manifest["sample_start"],
        "cutoff_date": manifest["cutoff_date"],
        "symbol_count": manifest["symbol_count"],
        "rows": manifest["rows"],
        "files": manifest["files"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
