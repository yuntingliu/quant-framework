"""Application service around the real-data barebone framework."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from alphalab import (
    ResultStore,
    SignalEngine,
    StrategyConfig,
    create_default_engine,
    create_runtime_engine,
    run_backtest,
)
from alphalab.analytics import PerformanceMetrics
from alphalab.dataio import DataEngine, MissingDataError
from alphalab.dataio.catalog import DataCatalog
from alphalab.strategies import list_strategy_files
from alphalab.utils.paths import APP_DATA_DIR, DATA_DIR, FACTOR_DIR, FUNDAMENTAL_DIR, MARKET_DIR


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest() -> dict:
    path = DATA_DIR / "manifest.json"
    if not path.exists():
        raise MissingDataError(f"Bundled data manifest is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset_status(path: Path, manifest: dict, key: str, *, mutable: bool = False) -> dict:
    relative = path.relative_to(DATA_DIR.parent).as_posix()
    expected = manifest.get("files", {}).get(relative, {})
    if not path.exists():
        return {"status": "missing", "path": relative, "bytes": 0}
    actual_hash = _hash_file(path)
    expected_hash = expected.get("sha256")
    return {
        "status": "ready" if mutable or not expected_hash or actual_hash == expected_hash else "invalid",
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": actual_hash,
    }


def list_provider_status() -> dict:
    manifest = load_manifest()
    engine = create_default_engine()
    runtime = DataCatalog().summary()
    return {
        "providers": engine.providers(),
        "active_profile": "demo",
        "profiles": {
            "demo": {
                "status": "ready",
                "latest_date": engine.get_latest_date(),
                "symbol_count": manifest.get("symbol_count", 0),
                "factor_returns": "ready",
            },
            "runtime": {
                "status": runtime["status"],
                "latest_date": next(
                    (
                        item["date_end"]
                        for item in runtime["datasets"]
                        if item["id"] == "rq.bars"
                    ),
                    None,
                ),
                "symbol_count": next(
                    (
                        item["symbol_count"]
                        for item in runtime["datasets"]
                        if item["id"] == "rq.bars"
                    ),
                    0,
                ),
                "factor_returns": "not_configured",
            },
        },
        "latest_date": engine.get_latest_date(),
        "sample_start": manifest.get("sample_start"),
        "symbol_count": manifest.get("symbol_count", 0),
        "realtime": {"status": "not_configured", "source": None},
        "datasets": {
            "market": _dataset_status(MARKET_DIR / "bars.parquet", manifest, "market"),
            "fundamentals": _dataset_status(FUNDAMENTAL_DIR / "fundamentals.parquet", manifest, "fundamentals"),
            "factors": _dataset_status(FACTOR_DIR / "factor_returns.parquet", manifest, "factors"),
            "app": _dataset_status(APP_DATA_DIR / "alphalab.db", manifest, "app", mutable=True),
        },
        "runtime": runtime,
    }


def _engine(profile: str) -> DataEngine:
    if profile == "demo":
        return create_default_engine()
    if profile == "runtime":
        return create_runtime_engine()
    raise ValueError("profile must be demo or runtime")


def _profile_range(profile: str) -> tuple[str, str]:
    if profile == "demo":
        manifest = load_manifest()
        return manifest["sample_start"], manifest["cutoff_date"]
    status = DataCatalog().status("rq.bars")
    if status["status"] != "ready" or not status["date_start"] or not status["date_end"]:
        raise MissingDataError("Runtime bars are not ready. Run an RQ data sync first.")
    return status["date_start"], status["date_end"]


def market_symbols(profile: str = "demo") -> list[str]:
    symbols = _engine(profile).get_symbols()
    if profile == "runtime" and not symbols:
        raise MissingDataError("Runtime bars are not ready. Run an RQ data sync first.")
    return symbols


def market_bars(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    profile: str = "demo",
) -> list[dict]:
    engine = _engine(profile)
    normalized = symbol.strip().upper()
    if normalized not in engine.get_symbols():
        raise KeyError(normalized)
    profile_start, profile_end = _profile_range(profile)
    start = start or profile_start
    end = end or profile_end
    if pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError("start must be on or before end")
    frame = engine.get_bars([normalized], start, end, strict=True, use_cache=False)
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    return frame.to_dict("records")


def factor_returns(
    names: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    profile: str = "demo",
) -> dict:
    if profile != "demo":
        if profile != "runtime":
            raise ValueError("profile must be demo or runtime")
        raise MissingDataError("Runtime factor returns are not configured")
    path = FACTOR_DIR / "factor_returns.parquet"
    if not path.exists():
        raise MissingDataError(f"Factor return file is missing: {path}")
    frame = pd.read_parquet(path)
    frame.index = pd.to_datetime(frame.index)
    requested = names or list(frame.columns)
    unknown = sorted(set(requested) - set(frame.columns))
    if unknown:
        raise KeyError(", ".join(unknown))
    if start:
        frame = frame.loc[frame.index >= pd.Timestamp(start)]
    if end:
        frame = frame.loc[frame.index <= pd.Timestamp(end)]
    values = frame[requested].reset_index().rename(columns={frame.index.name or "index": "date"})
    values["date"] = pd.to_datetime(values["date"]).dt.strftime("%Y-%m-%d")
    return {"names": requested, "rows": values.to_dict("records")}


def list_strategy_templates() -> list[dict]:
    rows = []
    for path in list_strategy_files():
        cfg = StrategyConfig.from_yaml(path)
        rows.append(
            {
                "id": cfg.name,
                "name": cfg.name,
                "description": cfg.description,
                "path": str(path),
                "factors": cfg.factor_names,
                "warnings": cfg.validate(),
            }
        )
    return rows


def get_strategy_template(strategy_id: str) -> dict | None:
    for item in list_strategy_templates():
        if item["id"] == strategy_id:
            path = Path(item["path"])
            item["yaml"] = path.read_text(encoding="utf-8")
            return item
    return None


def run_strategy_backtest(
    strategy_id: str,
    start_date: str,
    end_date: str,
    profile: str = "demo",
) -> dict:
    strategy = get_strategy_template(strategy_id)
    if strategy is None:
        raise KeyError(strategy_id)
    cfg = StrategyConfig.from_yaml(strategy["path"])
    returns, weights = run_backtest(
        cfg,
        start_date,
        end_date,
        data_engine=_engine(profile),
    )
    metrics = PerformanceMetrics.summarize(returns)
    store = ResultStore()
    try:
        store.register_strategy(cfg.name, strategy["path"], cfg.description)
        backtest_id = store.save_backtest(
            cfg.to_yaml(),
            returns,
            metrics,
            strategy_id=cfg.name,
            weights=weights,
            start_date=start_date,
            end_date=end_date,
        )
    finally:
        store.close()
    return {
        "id": backtest_id,
        "strategy_id": cfg.name,
        "profile": profile,
        "metrics": metrics,
        "returns": [
            {"date": str(date)[:10], "value": float(value)}
            for date, value in returns.items()
        ],
        "weights_count": int((weights.abs() > 0).sum().sum()) if not weights.empty else 0,
    }


def get_backtest(backtest_id: str) -> dict | None:
    store = ResultStore()
    try:
        rows = store.list_backtests(limit=1000)
        match = rows.loc[rows["id"].eq(backtest_id)]
        if match.empty:
            return None
        record = match.iloc[0].fillna("").to_dict()
        returns = store.load_returns(backtest_id).reset_index()
        weights = store.load_weights(backtest_id)
    finally:
        store.close()
    if not returns.empty:
        returns["date"] = returns["date"].dt.strftime("%Y-%m-%d")
    if not weights.empty:
        weights["date"] = weights["date"].dt.strftime("%Y-%m-%d")
    record["metrics"] = {
        "total_return": record.get("total_return", 0.0),
        "annual_return": record.get("annual_return", 0.0),
        "annual_vol": record.get("annual_vol", 0.0),
        "sharpe": record.get("sharpe", 0.0),
        "max_drawdown": record.get("max_drawdown", 0.0),
        "n_periods": record.get("n_periods", 0),
    }
    record["returns"] = [
        {
            "date": item["date"],
            "value": item["strategy"],
            "benchmark": item.get("benchmark"),
        }
        for item in returns.to_dict("records")
    ]
    record["weights"] = weights.to_dict("records")
    record["weights_count"] = len(record["weights"])
    return record


def generate_signal(
    strategy_id: str,
    as_of_date: str | None = None,
    persist: bool = True,
    profile: str = "demo",
) -> dict:
    strategy = get_strategy_template(strategy_id)
    if strategy is None:
        raise KeyError(strategy_id)
    _, profile_end = _profile_range(profile)
    signal_date = as_of_date or profile_end
    if pd.Timestamp(signal_date) > pd.Timestamp(profile_end):
        signal_date = profile_end
    cfg = StrategyConfig.from_yaml(strategy["path"])
    signal_engine = SignalEngine(_engine(profile))
    targets = signal_engine.generate_targets(cfg, signal_date)
    signal_id = None
    if persist:
        store = ResultStore()
        try:
            store.register_strategy(cfg.name, strategy["path"], cfg.description)
            signal_id = store.save_signal(cfg.name, signal_date, targets, status="paper")
        finally:
            store.close()
    return {
        "id": signal_id,
        "strategy_id": cfg.name,
        "profile": profile,
        "signal_date": signal_date,
        "targets": targets,
        "diagnostics": signal_engine.diagnostics,
    }


def latest_signal(strategy_id: str) -> dict | None:
    store = ResultStore()
    try:
        return store.get_latest_signal(strategy_id)
    finally:
        store.close()


def list_paper_orders(limit: int = 100) -> list[dict]:
    store = ResultStore()
    try:
        frame = store.list_orders(limit=limit)
    finally:
        store.close()
    return frame.fillna("").to_dict("records") if not frame.empty else []


def create_paper_order(
    symbol: str,
    action: str,
    quantity: float,
    price: float | None = None,
    signal_id: str | None = None,
) -> dict:
    engine = create_default_engine()
    if symbol not in engine.get_symbols():
        raise KeyError(symbol)
    if price is None:
        latest = engine.get_latest_date()
        bars = engine.get_bars([symbol], latest, latest, fields=["close"], use_cache=False)
        if bars.empty:
            raise MissingDataError(f"No bundled price for {symbol}")
        price = float(bars.iloc[-1]["close"])
    store = ResultStore()
    try:
        order_id = store.save_paper_order(symbol, action, quantity, price, signal_id=signal_id)
        row = store.list_orders(limit=100).loc[lambda frame: frame["id"].eq(order_id)].iloc[0]
    finally:
        store.close()
    return row.fillna("").to_dict()


def list_backtests(limit: int = 20) -> list[dict]:
    store = ResultStore()
    try:
        df = store.list_backtests(limit=limit)
    finally:
        store.close()
    if df.empty:
        return []
    return df.fillna("").to_dict("records")


def store_stats() -> dict:
    store = ResultStore()
    try:
        return store.stats()
    finally:
        store.close()
