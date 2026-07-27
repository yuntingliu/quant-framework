"""Application service around the real-data barebone framework."""
from __future__ import annotations

import hashlib
import json
import math
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
from alphalab.analytics import PerformanceMetrics, equal_weight_benchmark
from alphalab.dataio import DataEngine, MissingDataError
from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.fundamentals import CANONICAL_FIELDS
from alphalab.strategy import StrategyRepository
from alphalab.utils.paths import APP_DATA_DIR, DATA_DIR, FACTOR_DIR, FUNDAMENTAL_DIR, MARKET_DIR

FUNDAMENTAL_FIELDS = ("shares", "market_cap", *CANONICAL_FIELDS)


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
    runtime_factor_status = next(
        (
            item["status"]
            for item in runtime["datasets"]
            if item["id"] == "runtime.factor_returns"
        ),
        "missing",
    )
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
                "factor_returns": runtime_factor_status,
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


def fundamentals(
    symbols: list[str],
    fields: list[str] | None = None,
    start_quarter: str | None = None,
    end_quarter: str | None = None,
    asof_date: str | None = None,
    profile: str = "demo",
    limit: int = 100,
) -> dict:
    engine = _engine(profile)
    normalized_symbols = list(
        dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip())
    )
    if not normalized_symbols:
        raise ValueError("at least one symbol is required")
    unknown_symbols = sorted(set(normalized_symbols) - set(engine.get_symbols()))
    if unknown_symbols:
        raise KeyError(f"Unknown symbols: {unknown_symbols}")

    requested_fields = list(dict.fromkeys(fields or FUNDAMENTAL_FIELDS))
    unknown_fields = sorted(set(requested_fields) - set(FUNDAMENTAL_FIELDS))
    if unknown_fields:
        raise KeyError(f"Unknown fundamental fields: {unknown_fields}")

    profile_start, profile_end = _profile_range(profile)
    start_quarter = (start_quarter or _quarter_label(profile_start)).lower()
    end_quarter = (end_quarter or _quarter_label(profile_end)).lower()
    if start_quarter > end_quarter:
        raise ValueError("start_quarter must be on or before end_quarter")
    effective_asof = asof_date or profile_end
    frame = engine.get_fundamentals(
        normalized_symbols,
        requested_fields,
        start_quarter,
        end_quarter,
        asof_date=effective_asof,
        strict=True,
        use_cache=False,
    )
    preview = frame.head(limit).copy()
    if "available_date" in preview:
        preview["available_date"] = pd.to_datetime(
            preview["available_date"],
            errors="coerce",
        ).dt.strftime("%Y-%m-%d")
    return {
        "profile": profile,
        "symbols": normalized_symbols,
        "fields": requested_fields,
        "start_quarter": start_quarter,
        "end_quarter": end_quarter,
        "asof_date": effective_asof,
        "matched_rows": len(frame),
        "returned_rows": len(preview),
        "truncated": len(frame) > len(preview),
        "rows": preview.where(pd.notna(preview), None).to_dict("records"),
    }


def factor_returns(
    names: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    profile: str = "demo",
) -> dict:
    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
    available = {"MKT", "SMB", "HML", "MOM", "RMW", "rf"}
    requested = names or ["MKT", "SMB", "HML", "MOM", "RMW", "rf"]
    unknown = sorted(set(requested) - available)
    if unknown:
        raise KeyError(", ".join(unknown))
    if profile == "runtime":
        status = DataCatalog().status("runtime.factor_returns")
        if status["status"] != "ready":
            raise MissingDataError(
                "Runtime factor returns are missing. Run an RQ factors sync first."
            )
    engine = _engine(profile)
    profile_start, profile_end = _profile_range(profile)
    start = start or profile_start
    end = end or profile_end
    frame = engine.get_factors(
        requested,
        start,
        end,
        strict=True,
        use_cache=False,
    )
    values = frame.reset_index().rename(
        columns={frame.index.name or "index": "date"}
    )
    values["date"] = pd.to_datetime(values["date"]).dt.strftime("%Y-%m-%d")
    return {"names": requested, "rows": values.to_dict("records")}


def _quarter_label(value: str) -> str:
    timestamp = pd.Timestamp(value)
    return f"{timestamp.year}q{timestamp.quarter}"


def list_strategy_templates() -> list[dict]:
    activity = _strategy_activity()
    rows = []
    for definition in StrategyRepository().list():
        item = definition.as_dict()
        item.update(activity.get(definition.id, {}))
        rows.append(item)
    return rows


def get_strategy_template(strategy_id: str) -> dict | None:
    definition = StrategyRepository().get(strategy_id)
    if definition is None:
        return None
    item = definition.as_dict(include_yaml=True)
    item.update(_strategy_activity().get(definition.id, {}))
    return item


def validate_strategy_yaml(yaml_text: str) -> dict:
    return StrategyRepository.validate_yaml(yaml_text)


def clone_strategy(strategy_id: str, target_id: str) -> dict:
    return StrategyRepository().clone(strategy_id, target_id).as_dict(include_yaml=True)


def save_strategy(strategy_id: str, yaml_text: str) -> dict:
    return StrategyRepository().save(strategy_id, yaml_text).as_dict(include_yaml=True)


def delete_strategy(strategy_id: str) -> bool:
    return StrategyRepository().delete(strategy_id)


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
    engine = _engine(profile)
    returns, weights = run_backtest(
        cfg,
        start_date,
        end_date,
        data_engine=engine,
    )
    benchmark = equal_weight_benchmark(
        engine,
        list(cfg.universe.symbols) or engine.get_symbols(cfg.universe.pool),
        start_date,
        end_date,
        frequency=cfg.portfolio.rebalance_freq,
    ).reindex(returns.index)
    metrics = PerformanceMetrics.summarize(returns)
    store = ResultStore()
    try:
        store.register_strategy(cfg.name, strategy["path"], cfg.description)
        backtest_id = store.save_backtest(
            cfg.to_yaml(),
            returns,
            metrics,
            strategy_id=cfg.name,
            benchmark=benchmark,
            weights=weights,
            start_date=start_date,
            end_date=end_date,
            tags=[f"profile:{profile}"],
        )
    finally:
        store.close()
    return {
        "id": backtest_id,
        "strategy_id": cfg.name,
        "profile": profile,
        "metrics": metrics,
        "returns": [
            {
                "date": str(date)[:10],
                "value": float(value),
                "benchmark": (
                    float(benchmark.loc[date])
                    if date in benchmark.index and pd.notna(benchmark.loc[date])
                    else None
                ),
            }
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
    record["profile"] = _backtest_profile(record.get("tags"))
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
            signal_id = store.save_signal(
                cfg.name,
                signal_date,
                targets,
                status="paper",
                profile=profile,
            )
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


def latest_signal(strategy_id: str, profile: str = "demo") -> dict | None:
    store = ResultStore()
    try:
        return store.get_latest_signal(strategy_id, profile)
    finally:
        store.close()


def list_paper_orders(limit: int = 100) -> list[dict]:
    store = ResultStore()
    try:
        frame = store.list_orders(limit=limit)
    finally:
        store.close()
    return frame.fillna("").to_dict("records") if not frame.empty else []


def paper_account_summary(
    account_id: str = "paper",
    profile: str = "demo",
) -> dict:
    store = ResultStore()
    try:
        positions = store.list_paper_positions(account_id)
        symbols = positions["symbol"].astype(str).tolist() if not positions.empty else []
        latest_date, prices = _latest_prices(profile, symbols)
        account = store.mark_paper_positions(
            prices,
            latest_date,
            account_id=account_id,
        )
        positions = store.list_paper_positions(account_id)
        nav = store.paper_nav(account_id)
    finally:
        store.close()
    return {
        "account": account,
        "positions": (
            positions.fillna("").to_dict("records") if not positions.empty else []
        ),
        "nav": nav.fillna("").to_dict("records") if not nav.empty else [],
        "profile": profile,
        "price_date": latest_date,
    }


def list_paper_fills(
    account_id: str = "paper",
    limit: int = 100,
) -> list[dict]:
    store = ResultStore()
    try:
        frame = store.list_paper_fills(account_id, limit)
    finally:
        store.close()
    return frame.fillna("").to_dict("records") if not frame.empty else []


def preview_paper_rebalance(
    *,
    strategy_id: str | None = None,
    signal_id: str | None = None,
    profile: str = "demo",
    account_id: str = "paper",
) -> dict:
    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
    store = ResultStore()
    try:
        signal = store.get_signal(signal_id) if signal_id else None
        if signal is None and strategy_id:
            signal = store.get_latest_signal(strategy_id, profile)
        if signal is None:
            raise KeyError("signal")
        if signal.get("profile", "demo") != profile:
            raise ValueError("Signal profile does not match the requested paper profile")
        targets = {
            str(symbol).upper(): float(weight)
            for symbol, weight in signal.get("targets", {}).items()
            if float(weight) > 0
        }
        strategy = get_strategy_template(str(signal["strategy_id"]))
        maximum_weight_limit = 0.10
        if strategy is not None:
            maximum_weight_limit = StrategyConfig.from_yaml(
                strategy["path"]
            ).portfolio.max_weight
        positions = store.list_paper_positions(account_id)
        position_symbols = (
            positions["symbol"].astype(str).tolist() if not positions.empty else []
        )
        symbols = sorted(set(position_symbols) | set(targets))
        price_date, prices = _latest_prices(profile, symbols)
        account = store.mark_paper_positions(
            prices,
            price_date,
            account_id=account_id,
        )
        positions = store.list_paper_positions(account_id)
    finally:
        store.close()

    current = {
        str(row["symbol"]): float(row["quantity"])
        for row in positions.to_dict("records")
    }
    equity = float(account["equity"])
    orders = []
    for symbol in symbols:
        price = prices.get(symbol)
        if price is None or price <= 0:
            continue
        target_value = equity * targets.get(symbol, 0.0)
        target_quantity = math.floor(target_value / price / 100.0) * 100.0
        delta = target_quantity - current.get(symbol, 0.0)
        if abs(delta) < 1e-9:
            continue
        action = "buy" if delta > 0 else "sell"
        quantity = abs(delta)
        orders.append(
            {
                "symbol": symbol,
                "action": action,
                "quantity": quantity,
                "price": float(price),
                "notional": quantity * float(price),
                "commission": quantity * float(price) * 0.0003,
                "target_weight": targets.get(symbol, 0.0),
            }
        )
    sell_proceeds = sum(
        item["notional"] - item["commission"]
        for item in orders
        if item["action"] == "sell"
    )
    buy_cost = sum(
        item["notional"] + item["commission"]
        for item in orders
        if item["action"] == "buy"
    )
    projected_cash = float(account["cash"]) + sell_proceeds - buy_cost
    missing_prices = sorted(set(symbols) - set(prices))
    gross_target = float(sum(targets.values()))
    maximum_target = max(targets.values(), default=0.0)
    checks = [
        _risk_check("signal_targets", bool(targets), f"{len(targets)} targets"),
        _risk_check(
            "price_coverage",
            not missing_prices,
            "complete" if not missing_prices else f"missing {', '.join(missing_prices)}",
        ),
        _risk_check(
            "gross_target",
            gross_target <= 1.000001,
            f"{gross_target:.2%}",
        ),
        _risk_check(
            "max_weight",
            maximum_target <= maximum_weight_limit + 1e-6,
            f"{maximum_target:.2%} / limit {maximum_weight_limit:.2%}",
        ),
        _risk_check(
            "cash",
            projected_cash >= -1e-6,
            f"projected {projected_cash:,.2f}",
        ),
        _risk_check(
            "board_lot",
            all(item["quantity"] % 100 == 0 for item in orders),
            "100-share lots",
        ),
    ]
    allowed = all(item["passed"] for item in checks)
    turnover = (
        sum(item["notional"] for item in orders) / equity
        if equity > 0
        else 0.0
    )
    preview_payload = {
        "account_id": account_id,
        "signal_id": signal["id"],
        "strategy_id": signal["strategy_id"],
        "profile": profile,
        "price_date": price_date,
        "orders": orders,
    }
    preview_id = hashlib.sha256(
        json.dumps(preview_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {
        **preview_payload,
        "preview_id": preview_id,
        "allowed": allowed,
        "risk_status": "ready" if allowed else "blocked",
        "checks": checks,
        "account": account,
        "projected_cash": projected_cash,
        "turnover": turnover,
        "gross_target": gross_target,
        "maximum_target": maximum_target,
        "maximum_weight_limit": maximum_weight_limit,
        "disclaimer": "Paper simulation only; no broker order is created.",
    }


def execute_paper_rebalance(
    *,
    strategy_id: str | None = None,
    signal_id: str | None = None,
    profile: str = "demo",
    account_id: str = "paper",
    confirm: bool = False,
) -> dict:
    if not confirm:
        raise PermissionError("Paper rebalance requires explicit confirmation")
    preview = preview_paper_rebalance(
        strategy_id=strategy_id,
        signal_id=signal_id,
        profile=profile,
        account_id=account_id,
    )
    if not preview["allowed"]:
        raise ValueError("Paper rebalance is blocked by risk checks")
    store = ResultStore()
    try:
        result = store.apply_paper_orders(
            preview["orders"],
            account_id=account_id,
            signal_id=preview["signal_id"],
            nav_date=preview["price_date"],
        )
        positions = store.list_paper_positions(account_id)
    finally:
        store.close()
    return {
        "preview_id": preview["preview_id"],
        "status": "filled",
        "orders_created": len(result["order_ids"]),
        "order_ids": result["order_ids"],
        "fill_ids": result["fill_ids"],
        "account": result["account"],
        "positions": positions.fillna("").to_dict("records"),
    }


def create_paper_order(
    symbol: str,
    action: str,
    quantity: float,
    price: float | None = None,
    signal_id: str | None = None,
    profile: str = "demo",
    account_id: str = "paper",
) -> dict:
    engine = _engine(profile)
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
        order_id = store.save_paper_order(
            symbol,
            action,
            quantity,
            price,
            signal_id=signal_id,
            account_id=account_id,
        )
        row = (
            store.list_orders(limit=100)
            .loc[lambda frame: frame["id"].eq(order_id)]
            .iloc[0]
        )
    finally:
        store.close()
    return row.fillna("").to_dict()


def _latest_prices(
    profile: str,
    symbols: list[str],
) -> tuple[str, dict[str, float]]:
    engine = _engine(profile)
    latest = engine.get_latest_date()
    if latest is None:
        raise MissingDataError(f"{profile} market data is not ready")
    if not symbols:
        return latest, {}
    start = (pd.Timestamp(latest) - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    bars = engine.get_bars(
        symbols,
        start,
        latest,
        fields=["close"],
        strict=False,
        use_cache=False,
    )
    if bars.empty:
        return latest, {}
    frame = bars.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    latest_rows = frame.sort_values("date").groupby("symbol", as_index=False).tail(1)
    return latest, {
        str(row["symbol"]).upper(): float(row["close"])
        for row in latest_rows.to_dict("records")
        if pd.notna(row["close"])
    }


def _risk_check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def list_backtests(limit: int = 20) -> list[dict]:
    store = ResultStore()
    try:
        df = store.list_backtests(limit=limit)
    finally:
        store.close()
    if df.empty:
        return []
    records = df.fillna("").to_dict("records")
    for record in records:
        record["profile"] = _backtest_profile(record.get("tags"))
    return records


def _backtest_profile(tags: object) -> str:
    if isinstance(tags, str) and tags:
        try:
            values = json.loads(tags)
        except json.JSONDecodeError:
            values = []
    elif isinstance(tags, list):
        values = tags
    else:
        values = []
    for value in values:
        if isinstance(value, str) and value.startswith("profile:"):
            profile = value.removeprefix("profile:")
            if profile in {"demo", "runtime"}:
                return profile
    return "demo"


def _strategy_activity() -> dict[str, dict]:
    store = ResultStore()
    try:
        backtests = store.list_backtests(limit=1000)
        strategies = store.list_strategies()
        latest_signals = {}
        for strategy_id in strategies.get("id", pd.Series(dtype=str)).astype(str):
            signal = store.get_latest_signal(strategy_id)
            if signal:
                latest_signals[strategy_id] = signal
    finally:
        store.close()

    activity: dict[str, dict] = {}
    if not backtests.empty:
        ordered = backtests.sort_values("run_at", ascending=False)
        for strategy_id, group in ordered.groupby("strategy_id", dropna=True):
            latest = group.iloc[0]
            sharpe = latest.get("sharpe")
            activity[str(strategy_id)] = {
                "latest_backtest": {
                    "id": latest["id"],
                    "run_at": latest.get("run_at"),
                    "total_return": latest.get("total_return"),
                    "sharpe": sharpe,
                    "max_drawdown": latest.get("max_drawdown"),
                    "profile": _backtest_profile(latest.get("tags")),
                },
                "research_status": (
                    "watch"
                    if pd.notna(sharpe) and float(sharpe) > 0
                    else "weak"
                ),
            }
    for strategy_id, signal in latest_signals.items():
        activity.setdefault(strategy_id, {})["latest_signal_date"] = signal["signal_date"]
    return activity


def store_stats() -> dict:
    store = ResultStore()
    try:
        return store.stats()
    finally:
        store.close()
