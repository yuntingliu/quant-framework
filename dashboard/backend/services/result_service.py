"""Persisted backtest and paper-simulation services."""
from __future__ import annotations

import hashlib
import json
import math

import pandas as pd

from alphalab import PipelineRepository, ResultStore
from alphalab.dataio import MissingDataError
from dashboard.backend.services.data_service import _engine


def _json_payload(value: object, default: object) -> object:
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


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


def get_backtest(backtest_id: str) -> dict | None:
    store = ResultStore()
    try:
        record = store.get_backtest_record(backtest_id)
        if record is None:
            return None
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
        {"date": item["date"], "value": item["strategy"], "benchmark": item.get("benchmark")}
        for item in returns.to_dict("records")
    ]
    record["weights"] = weights.to_dict("records")
    record["weights_count"] = len(record["weights"])
    record["profile"] = _backtest_profile(record.get("tags"))
    record["provenance"] = _json_payload(record.pop("provenance_json", None), {})
    record["executions"] = _json_payload(record.pop("execution_json", None), [])
    record["component_manifest"] = _json_payload(
        record.pop("component_manifest_json", None), []
    )
    record["settings"] = _json_payload(record.pop("settings_json", None), {})
    return record


def list_backtests(limit: int = 20) -> list[dict]:
    store = ResultStore()
    try:
        frame = store.list_backtests(limit=limit)
    finally:
        store.close()
    if frame.empty:
        return []
    records = frame.fillna("").to_dict("records")
    for record in records:
        record["profile"] = _backtest_profile(record.get("tags"))
    return records


def list_paper_orders(limit: int = 100) -> list[dict]:
    store = ResultStore()
    try:
        frame = store.list_orders(limit=limit)
    finally:
        store.close()
    return frame.fillna("").to_dict("records") if not frame.empty else []


def paper_account_summary(account_id: str = "paper", profile: str = "demo") -> dict:
    store = ResultStore()
    try:
        positions = store.list_paper_positions(account_id)
        symbols = positions["symbol"].astype(str).tolist() if not positions.empty else []
        latest_date, prices = _latest_prices(profile, symbols)
        account = store.mark_paper_positions(prices, latest_date, account_id=account_id)
        positions = store.list_paper_positions(account_id)
        nav = store.paper_nav(account_id)
    finally:
        store.close()
    return {
        "account": account,
        "positions": positions.fillna("").to_dict("records") if not positions.empty else [],
        "nav": nav.fillna("").to_dict("records") if not nav.empty else [],
        "profile": profile,
        "price_date": latest_date,
    }


def list_paper_fills(account_id: str = "paper", limit: int = 100) -> list[dict]:
    store = ResultStore()
    try:
        frame = store.list_paper_fills(account_id, limit)
    finally:
        store.close()
    return frame.fillna("").to_dict("records") if not frame.empty else []


def _project_max_weight(project_id: str) -> float:
    repository = PipelineRepository()
    try:
        project = repository.get_project(project_id, include_source=False)
    finally:
        repository.close()
    if project is None:
        return 0.10
    risk = next(
        (item for item in project["component_manifest"] if item["stage"] == "risk"),
        {},
    )
    return float(risk.get("parameters", {}).get("max_weight", 0.10))


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
        maximum_weight_limit = _project_max_weight(str(signal["strategy_id"]))
        positions = store.list_paper_positions(account_id)
        position_symbols = positions["symbol"].astype(str).tolist() if not positions.empty else []
        symbols = sorted(set(position_symbols) | set(targets))
        price_date, prices = _latest_prices(profile, symbols)
        account = store.mark_paper_positions(prices, price_date, account_id=account_id)
        positions = store.list_paper_positions(account_id)
    finally:
        store.close()

    current = {str(row["symbol"]): float(row["quantity"]) for row in positions.to_dict("records")}
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
        quantity = abs(delta)
        orders.append(
            {
                "symbol": symbol,
                "action": "buy" if delta > 0 else "sell",
                "quantity": quantity,
                "price": float(price),
                "notional": quantity * float(price),
                "commission": quantity * float(price) * 0.0003,
                "target_weight": targets.get(symbol, 0.0),
            }
        )
    sell_proceeds = sum(
        item["notional"] - item["commission"] for item in orders if item["action"] == "sell"
    )
    buy_cost = sum(
        item["notional"] + item["commission"] for item in orders if item["action"] == "buy"
    )
    projected_cash = float(account["cash"]) + sell_proceeds - buy_cost
    missing_prices = sorted(set(symbols) - set(prices))
    gross_target = float(sum(targets.values()))
    maximum_target = max(targets.values(), default=0.0)
    checks = [
        _risk_check("signal_targets", bool(targets), f"{len(targets)} targets"),
        _risk_check("price_coverage", not missing_prices, "complete" if not missing_prices else f"missing {', '.join(missing_prices)}"),
        _risk_check("gross_target", gross_target <= 1.000001, f"{gross_target:.2%}"),
        _risk_check("max_weight", maximum_target <= maximum_weight_limit + 1e-6, f"{maximum_target:.2%} / limit {maximum_weight_limit:.2%}"),
        _risk_check("cash", projected_cash >= -1e-6, f"projected {projected_cash:,.2f}"),
        _risk_check("board_lot", all(item["quantity"] % 100 == 0 for item in orders), "100-share lots"),
    ]
    allowed = all(item["passed"] for item in checks)
    turnover = sum(item["notional"] for item in orders) / equity if equity > 0 else 0.0
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
        row = store.list_orders(limit=100).loc[lambda frame: frame["id"].eq(order_id)].iloc[0]
    finally:
        store.close()
    return row.fillna("").to_dict()


def _latest_prices(profile: str, symbols: list[str]) -> tuple[str, dict[str, float]]:
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


__all__ = [
    "create_paper_order",
    "execute_paper_rebalance",
    "get_backtest",
    "list_backtests",
    "list_paper_fills",
    "list_paper_orders",
    "paper_account_summary",
    "preview_paper_rebalance",
]
