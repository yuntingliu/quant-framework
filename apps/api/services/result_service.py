"""Persisted backtest and paper-simulation services."""

from __future__ import annotations

import hashlib
import json
import math
import re

import pandas as pd

from alphalab import ResultStore, StrategyRepository
from alphalab.dataio import MissingDataError
from apps.api.services.data_service import _engine

_EXECUTION_COUNTER_FIELDS = (
    "attempted_trade_count",
    "successful_trade_count",
    "execution_data_fill_count",
    "synthetic_state_count",
    "market_state_rejection_count",
    "suspension_rejection_count",
    "limit_up_rejection_count",
    "limit_down_rejection_count",
    "capacity_rejection_count",
    "cash_rejection_count",
)


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
    record["events"] = _json_payload(record.pop("event_json", None), [])
    record["attribution"] = _json_payload(record.pop("attribution_json", None), {})
    record["validation_output"] = _json_payload(record.pop("validation_output_json", None), {})
    record["run_diagnostics"] = _json_payload(record.pop("run_diagnostics_json", None), {})
    record["component_manifest"] = _json_payload(record.pop("component_manifest_json", None), [])
    record["strategy_manifest"] = _json_payload(record.pop("strategy_manifest_json", None), [])
    record["settings"] = _json_payload(record.pop("settings_json", None), {})
    record["project_id"] = record.get("strategy_project_id") or record.get("strategy_id")
    record["project_strategy_id"] = record.get("project_strategy_id") or "main"
    record["strategy_name"] = record.get("strategy_name") or record.get("strategy_id")
    return record


def _head_tail(rows: list[dict], sample_size: int = 3) -> dict:
    return {
        "count": len(rows),
        "head": rows[:sample_size],
        "tail": rows[-sample_size:] if len(rows) > sample_size else [],
        "truncated": len(rows) > sample_size * 2,
    }


def _execution_sample(row: dict) -> dict:
    executed = row.get("executed_weights")
    executed_symbols = list(executed) if isinstance(executed, dict) else []
    constrained = row.get("constrained_symbols")
    constrained_symbols = list(constrained) if isinstance(constrained, list) else []
    return {
        key: row.get(key)
        for key in (
            "decision_date",
            "entry_date",
            "activation",
            "decision_reason",
            "target_count",
            "executed_count",
            "traded_weight",
            "turnover",
            "total_cost",
            "execution_fidelity",
            "target_weight_deviation",
            "attempted_trade_count",
            "successful_trade_count",
            "synthetic_state_count",
            "market_state_rejection_count",
            "suspension_rejection_count",
            "limit_up_rejection_count",
            "limit_down_rejection_count",
            "capacity_rejection_count",
            "cash_rejection_count",
        )
        if key in row
    } | {
        "executed_symbol_count": len(executed_symbols),
        "executed_symbols_sample": executed_symbols[:5],
        "constrained_symbol_count": len(constrained_symbols),
        "constrained_symbols_sample": constrained_symbols[:5],
    }


def _public_event_value(value: object) -> object:
    if isinstance(value, str):
        text = value.splitlines()[0]
        text = re.sub(r"[A-Za-z]:\\[^\s\"']+", "<internal-path>", text)
        text = re.sub(
            r"(?<![:/\w])/(?!/)(?:[^/\s]+/)+[^\s\"']+",
            "<internal-path>",
            text,
        )
        text = re.sub(r"\b[a-fA-F0-9]{40,64}\b", "<internal-id>", text)
        return text[:500]
    if isinstance(value, list):
        return [_public_event_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _public_event_value(item)
            for key, item in value.items()
            if key
            not in {
                "environment",
                "environment_sha256",
                "path",
                "root",
                "source",
                "source_sha256",
                "state_sha256",
                "stderr",
                "stdout",
                "traceback",
            }
        }
    return value


def _execution_summary(record: dict) -> dict[str, int]:
    diagnostics = record.get("run_diagnostics")
    if not isinstance(diagnostics, dict) or not diagnostics:
        diagnostics = record.get("execution") if isinstance(record.get("execution"), dict) else {}
    saved = (
        diagnostics.get("execution_summary")
        if isinstance(diagnostics.get("execution_summary"), dict)
        else {}
    )
    executions = [item for item in record.get("executions") or () if isinstance(item, dict)]
    attempted = sum(
        int(item.get("attempted_trade_count", item.get("target_count", 0)) or 0)
        for item in executions
    )
    successful = sum(
        int(item.get("successful_trade_count", item.get("executed_count", 0)) or 0)
        for item in executions
    )
    rejected = sum(
        int(
            item.get(
                "market_state_rejection_count",
                len(item.get("missing_execution_data") or ()),
            )
            or 0
        )
        for item in executions
    )
    filled = sum(
        sum(
            int(value or 0)
            for value in (item.get("execution_data_fill") or {}).get("filled", {}).values()
        )
        for item in executions
    )
    nested_fill = diagnostics.get("execution_data_fill")
    nested_fill = nested_fill if isinstance(nested_fill, dict) else {}
    summary = {
        "attempted_trade_count": int(
            record.get("attempted_trade_count", saved.get("attempted_trade_count", attempted)) or 0
        ),
        "successful_trade_count": int(
            record.get("successful_trade_count", saved.get("successful_trade_count", successful))
            or 0
        ),
        "execution_data_fill_count": int(
            record.get(
                "execution_data_fill_count",
                saved.get("execution_data_fill_count", nested_fill.get("value_count", filled)),
            )
            or 0
        ),
        "market_state_rejection_count": int(
            record.get(
                "market_state_rejection_count",
                saved.get(
                    "market_state_rejection_count",
                    (diagnostics.get("execution_data_exclusions") or {}).get(
                        "symbol_date_count", rejected
                    ),
                ),
            )
            or 0
        ),
    }
    for field in _EXECUTION_COUNTER_FIELDS[3:]:
        fallback = (
            summary["execution_data_fill_count"]
            if field == "synthetic_state_count"
            else summary["market_state_rejection_count"]
            if field == "market_state_rejection_count"
            else sum(int(item.get(field) or 0) for item in executions)
        )
        summary[field] = int(record.get(field, saved.get(field, fallback)) or 0)
    return summary


def build_backtest_summary(record: dict) -> dict:
    """Build the one compact public contract used by jobs and frozen Runs."""

    returns = list(record.get("returns") or [])
    weights = list(record.get("weights") or [])
    executions = list(record.get("executions") or [])
    events = list(record.get("events") or [])
    diagnostics = record.get("run_diagnostics")
    if not isinstance(diagnostics, dict) or not diagnostics:
        diagnostics = record.get("execution") if isinstance(record.get("execution"), dict) else {}
    validation_output = record.get("validation_output") or {}
    assessment = validation_output.get("research_quality", record.get("research_assessment"))
    execution_reliable = record.get("execution_reliable", diagnostics.get("execution_reliable"))
    warning_values = [
        *(record.get("warnings") or ()),
        *(diagnostics.get("warnings") or ()),
        *((record.get("validation_output") or {}).get("warnings") or ()),
        *((assessment or {}).get("warnings") or ()),
    ]
    warnings = list(dict.fromkeys(str(item) for item in warning_values if str(item).strip()))
    execution_summary = _execution_summary(record)
    raw_research_valid = record.get("research_valid", diagnostics.get("research_valid"))
    research_valid = bool(raw_research_valid) if raw_research_valid is not None else False
    raw_invalid_reasons = record.get(
        "research_invalid_reasons", diagnostics.get("research_invalid_reasons")
    )
    research_invalid_reasons = [
        str(value)
        for value in (raw_invalid_reasons or ())
        if isinstance(value, str) and value.strip()
    ]
    if raw_research_valid is None and not research_invalid_reasons:
        research_invalid_reasons = ["UNVERIFIED_EXECUTION_FIDELITY"]
    if isinstance(assessment, dict):
        research_valid = assessment["passed"]
        research_invalid_reasons = list(assessment["reasons"])
    elif execution_reliable is not None:
        # New runs of old/custom validation source have no implicit hidden quality evaluator.
        research_valid = None
        research_invalid_reasons = ["RESEARCH_QUALITY_NOT_EVALUATED"]
    execution_fidelity = diagnostics.get("execution_fidelity")
    if not isinstance(execution_fidelity, dict):
        execution_fidelity = {}
    period = record.get("period") if isinstance(record.get("period"), dict) else {}
    counts = record.get("counts") if isinstance(record.get("counts"), dict) else {}
    samples = record.get("samples") if isinstance(record.get("samples"), dict) else None
    if samples is None:
        samples = {
            "returns": _head_tail(returns),
            "executions": _head_tail(
                [_public_event_value(_execution_sample(item)) for item in executions]
            ),
        }
    return {
        "status": "succeeded",
        "backtest_id": record.get("backtest_id") or record.get("id"),
        "error_code": None,
        "error_summary": None,
        "project_id": record.get("project_id")
        or record.get("strategy_project_id")
        or record.get("strategy_id"),
        "strategy_id": record.get("project_strategy_id") or "main",
        "strategy_name": record.get("strategy_name"),
        "batch_id": record.get("batch_id"),
        "metrics": dict(record.get("metrics") or {}),
        "period": {
            "start_date": period.get("start_date", record.get("start_date")),
            "end_date": period.get("end_date", record.get("end_date")),
        },
        "counts": {
            "return_rows": int(counts.get("return_rows", len(returns)) or 0),
            "weight_rows": int(counts.get("weight_rows", len(weights)) or 0),
            "executions": int(counts.get("executions", len(executions)) or 0),
            "events": int(counts.get("events", len(events)) or 0),
        },
        "samples": _public_event_value(samples),
        "warnings": [_public_event_value(str(item)) for item in warnings[:10]],
        "warnings_truncated": bool(record.get("warnings_truncated")) or len(warnings) > 10,
        "research_valid": research_valid,
        "research_invalid_reasons": research_invalid_reasons,
        "research_assessment": _public_event_value(assessment),
        "execution_reliable": execution_reliable,
        "execution_invalid_reasons": list(record.get(
            "execution_invalid_reasons", diagnostics.get("execution_invalid_reasons")
        ) or []),
        "execution_fidelity": _public_event_value(execution_fidelity),
        **execution_summary,
    }


def get_backtest_summary(backtest_id: str) -> dict | None:
    record = get_backtest(backtest_id)
    return build_backtest_summary(record) if record is not None else None


def get_backtest_event_page(
    backtest_id: str,
    *,
    kind: str,
    offset: int,
    limit: int,
) -> dict | None:
    record = get_backtest(backtest_id)
    if record is None:
        return None
    collections = {
        "executions": list(record.get("executions") or []),
        "events": list(record.get("events") or []),
    }
    if kind not in collections:
        raise ValueError("kind must be executions or events")
    rows = collections[kind]
    start = max(0, int(offset))
    size = max(1, min(int(limit), 100))
    page = [_public_event_value(item) for item in rows[start : start + size]]
    return {
        "status": "succeeded",
        "backtest_id": record["id"],
        "kind": kind,
        "offset": start,
        "limit": size,
        "total": len(rows),
        "next_offset": start + len(page) if start + len(page) < len(rows) else None,
        "rows": page,
    }


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
        record["project_id"] = record.get("strategy_project_id") or record.get("strategy_id")
        record["project_strategy_id"] = record.get("project_strategy_id") or "main"
        record["strategy_name"] = record.get("strategy_name") or record.get("strategy_id")
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
    repository = StrategyRepository()
    try:
        project = repository.get_project(project_id, include_source=False)
    finally:
        repository.close()
    if project is None:
        return 0.10
    return float(project.get("settings", {}).get("max_weight", 1.0))


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
        _risk_check(
            "price_coverage",
            not missing_prices,
            "complete" if not missing_prices else f"missing {', '.join(missing_prices)}",
        ),
        _risk_check("gross_target", gross_target <= 1.000001, f"{gross_target:.2%}"),
        _risk_check(
            "max_weight",
            maximum_target <= maximum_weight_limit + 1e-6,
            f"{maximum_target:.2%} / limit {maximum_weight_limit:.2%}",
        ),
        _risk_check("cash", projected_cash >= -1e-6, f"projected {projected_cash:,.2f}"),
        _risk_check(
            "board_lot", all(item["quantity"] % 100 == 0 for item in orders), "100-share lots"
        ),
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
    "build_backtest_summary",
    "create_paper_order",
    "execute_paper_rebalance",
    "get_backtest",
    "get_backtest_event_page",
    "get_backtest_summary",
    "list_backtests",
    "list_paper_fills",
    "list_paper_orders",
    "paper_account_summary",
    "preview_paper_rebalance",
]
