"""Persisted backtest and paper-simulation services."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import replace

import pandas as pd

from alphalab import ResultStore, StrategyRepository
from alphalab.analytics.holdings_risk import (
    HoldingsRiskParameters,
    analyse,
    frozen_inputs,
    input_hash,
)
from alphalab.dataio import MissingDataError
from alphalab.execution.risk_guard import (
    AccountSnapshot,
    PositionSnapshot,
    account_risk_metrics,
    evaluate,
    pause_breaches,
    resume_decision,
)
from alphalab.execution.risk_policy import RiskPolicy, default_policy, policy_from_payload
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


# -- risk policy and paper risk -------------------------------------------


class RiskBlockedError(ValueError):
    """A hard risk rule refused the proposal; ``detail`` carries the findings."""

    def __init__(self, detail: dict):
        super().__init__(str(detail.get("message") or "Blocked by a risk rule"))
        self.detail = detail


class StalePreviewError(ValueError):
    """The approved preview no longer describes the account it would change."""

    def __init__(self, detail: dict):
        super().__init__(str(detail.get("message") or "The approved preview is out of date"))
        self.detail = detail


def _blocked_detail(verdict) -> dict:
    first = verdict.blocking[0] if verdict.blocking else None
    return {
        "message": (
            f"Blocked by {len(verdict.blocking)} risk rule(s): {first.message}"
            if first is not None
            else "Blocked by a risk rule"
        ),
        "policy": verdict.policy.label,
        "preview_id": verdict.preview_id,
        "blocking": [item.as_dict() for item in verdict.blocking],
        "warnings": [item.as_dict() for item in verdict.warnings],
        "info": [item.as_dict() for item in verdict.info],
        "checks": list(verdict.checks),
    }


def _blocked_detail_from_preview(preview: dict) -> dict:
    blocking = list(preview.get("blocking") or [])
    first = blocking[0] if blocking else None
    return {
        "message": (
            f"Blocked by {len(blocking)} risk rule(s): {first['message']}"
            if first is not None
            else "Blocked by a risk rule"
        ),
        "policy": (preview.get("policy") or {}).get("label"),
        "preview_id": preview.get("preview_id"),
        "blocking": blocking,
        "warnings": list(preview.get("warnings") or []),
        "info": list(preview.get("info") or []),
        "checks": list(preview.get("checks") or []),
    }


def paper_risk_policy(account_id: str = "paper", strategy_id: str | None = None) -> dict:
    """The policy in force plus its full version history."""
    store = ResultStore()
    try:
        policy = _policy_for(store, account_id, strategy_id)
        versions = store.list_risk_policy_versions(policy.policy_id)
        account = store.paper_account(account_id)
    finally:
        store.close()
    return {
        "active": policy.to_json(),
        "versions": [_policy_version_summary(item) for item in versions],
        "account": account,
        "scope": {"account_id": account_id, "strategy_id": strategy_id},
    }


def _policy_version_summary(record: dict) -> dict:
    policy = RiskPolicy.from_record(record)
    return {
        "policy_id": policy.policy_id,
        "version": policy.version,
        "reason": policy.reason,
        "author": policy.author,
        "created_at": policy.created_at,
        "limits": policy.limits.as_dict(),
        "pause": policy.pause.as_dict(),
        "label": policy.label,
    }


def publish_paper_risk_policy(
    payload: dict,
    *,
    account_id: str = "paper",
    strategy_id: str | None = None,
) -> dict:
    """Append a new policy version and activate it. Old versions are untouched."""
    store = ResultStore()
    try:
        current = _policy_for(store, account_id, strategy_id)
        if strategy_id and not payload.get("scope"):
            payload = {**payload, "scope": "strategy", "scope_id": strategy_id}
        proposal = policy_from_payload(payload, base=current)
        saved = store.save_risk_policy(proposal.to_record())
        store.activate_risk_policy(
            account_id,
            proposal.policy_id,
            proposal.version,
            scope=proposal.scope,
            scope_id=proposal.scope_id,
            reason=proposal.reason,
        )
        versions = store.list_risk_policy_versions(proposal.policy_id)
    finally:
        store.close()
    return {
        "active": RiskPolicy.from_record(saved).to_json(),
        "versions": [_policy_version_summary(item) for item in versions],
    }


def resume_paper_risk(account_id: str = "paper", reason: str = "") -> dict:
    """Clear a pause explicitly, recording why."""
    if not str(reason or "").strip():
        raise ValueError("resume requires a reason")
    store = ResultStore()
    try:
        account = store.resume_paper_account(account_id, reason)
        store.save_risk_evaluation(
            {
                "account_id": account_id,
                "door": "manual_resume",
                "verdict": "resumed",
                "findings": [
                    {
                        "rule": "resume",
                        "severity": "info",
                        "message": str(reason),
                        "observed": None,
                        "limit": None,
                        "symbol": None,
                    }
                ],
                "orders": [],
                "snapshot_id": "",
                "policy_id": "",
                "policy_version": 0,
            }
        )
    finally:
        store.close()
    return {"account": account, "reason": reason}


def list_paper_risk_evaluations(account_id: str = "paper", limit: int = 50) -> list[dict]:
    store = ResultStore()
    try:
        return store.list_risk_evaluations(account_id, limit=limit)
    finally:
        store.close()


def _project_max_weight(project_id: str) -> float:
    repository = StrategyRepository()
    try:
        project = repository.get_project(project_id, include_source=False)
    finally:
        repository.close()
    if project is None:
        return 0.10
    return float(project.get("settings", {}).get("max_weight", 1.0))


def _policy_for(store: ResultStore, account_id: str, strategy_id: str | None = None) -> RiskPolicy:
    record = store.active_risk_policy(account_id, strategy_id)
    if record is None:
        seeded = default_policy()
        store.save_risk_policy(seeded.to_record())
        store.activate_risk_policy(
            account_id,
            seeded.policy_id,
            seeded.version,
            reason="Seeded baseline policy for this paper account",
        )
        record = store.active_risk_policy(account_id, strategy_id)
    if record is None:
        raise ValueError("no risk policy could be activated for this paper account")
    return RiskPolicy.from_record(record)


def _average_daily_volumes(
    profile: str, symbols: list[str], price_date: str | None, lookback_days: int
) -> dict[str, float]:
    if not symbols or not price_date:
        return {}
    try:
        engine = _engine(profile)
        start = (
            pd.Timestamp(price_date) - pd.Timedelta(days=max(lookback_days * 2, 45))
        ).strftime("%Y-%m-%d")
        bars = engine.get_bars(
            symbols,
            start,
            price_date,
            fields=["close", "volume"],
            strict=False,
            use_cache=False,
        )
    except (KeyError, ValueError, MissingDataError):
        return {}
    if bars.empty or "volume" not in bars.columns:
        return {}
    frame = bars.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    recent = frame.sort_values("date").groupby("symbol").tail(int(lookback_days))
    volumes = recent.groupby("symbol")["volume"].mean()
    return {
        str(symbol).upper(): float(value)
        for symbol, value in volumes.items()
        if pd.notna(value) and float(value) > 0
    }


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


def _build_snapshot(
    store: ResultStore,
    *,
    account_id: str,
    profile: str,
    symbols: list[str],
    strategy_id: str | None = None,
) -> tuple:
    """Freeze the account, decide a pause or resume, and return (snapshot, policy)."""
    policy = _policy_for(store, account_id, strategy_id)
    price_date, prices = _latest_prices(profile, symbols)
    account = store.mark_paper_positions(prices, price_date, account_id=account_id)
    positions_frame = store.list_paper_positions(account_id)
    nav_frame = store.paper_nav(account_id, limit=500)
    volumes = _average_daily_volumes(
        profile, symbols, price_date, policy.limits.volume_lookback_days
    )
    positions = []
    for row in positions_frame.to_dict("records"):
        symbol = str(row["symbol"]).upper()
        price = prices.get(symbol)
        if price is None:
            market_price = row.get("market_price")
            price = float(market_price) if pd.notna(market_price) else None
        quantity = float(row["quantity"])
        positions.append(
            PositionSnapshot(
                symbol=symbol,
                quantity=quantity,
                avg_cost=float(row.get("avg_cost") or 0.0),
                price=price,
                market_value=(quantity * float(price)) if price is not None else None,
            )
        )
    snapshot = AccountSnapshot(
        account_id=account_id,
        cash=float(account["cash"]),
        initial_cash=float(account.get("initial_cash") or account["cash"]),
        equity=float(account["equity"]),
        positions=tuple(positions),
        prices={key: float(value) for key, value in prices.items()},
        volumes=volumes,
        nav=tuple(
            (str(row["date"]), float(row["equity"]))
            for row in nav_frame.to_dict("records")
            if pd.notna(row.get("equity"))
        ),
        price_date=price_date,
        status=str(account.get("status") or "active"),
        pause_reason=account.get("pause_reason"),
        paused_at=account.get("paused_at"),
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    if snapshot.status == "paused":
        allowed, reason = resume_decision(
            policy,
            account_risk_metrics(snapshot),
            paused_at=snapshot.paused_at,
        )
        if allowed:
            store.resume_paper_account(account_id, reason)
            snapshot = replace(snapshot, status="active", pause_reason=None, paused_at=None)
    return snapshot, policy


def _persist_pause(store: ResultStore, policy: RiskPolicy, snapshot: AccountSnapshot) -> None:
    if snapshot.status == "paused":
        return
    reasons = pause_breaches(policy, account_risk_metrics(snapshot))
    if reasons:
        store.pause_paper_account(snapshot.account_id, "; ".join(reasons))


def _record_evaluation(
    store: ResultStore,
    verdict,
    *,
    door: str,
    orders: list[dict],
    strategy_id: str | None = None,
    signal_id: str | None = None,
    preview_id: str | None = None,
) -> dict:
    snapshot_id = store.save_risk_snapshot(verdict.snapshot.snapshot_record())
    evaluation_id = store.save_risk_evaluation(
        {
            "account_id": verdict.snapshot.account_id,
            "door": door,
            "strategy_id": strategy_id,
            "signal_id": signal_id,
            "preview_id": preview_id or verdict.preview_id,
            "snapshot_id": snapshot_id,
            "policy_id": verdict.policy.policy_id,
            "policy_version": verdict.policy.version,
            "verdict": verdict.status,
            "findings": [item.as_dict() for item in verdict.findings],
            "orders": orders,
        }
    )
    return {"snapshot_id": snapshot_id, "evaluation_id": evaluation_id}


def _target_orders(snapshot: AccountSnapshot, targets: dict[str, float]) -> list[dict]:
    equity = float(snapshot.equity)
    symbols = sorted(set(snapshot.prices) | set(targets))
    orders: list[dict] = []
    for symbol in symbols:
        price = snapshot.prices.get(symbol) or snapshot.price(symbol)
        if price is None or price <= 0:
            continue
        target_value = equity * float(targets.get(symbol, 0.0))
        target_quantity = math.floor(target_value / price / 100.0) * 100.0
        delta = target_quantity - snapshot.quantity(symbol)
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
                "target_weight": float(targets.get(symbol, 0.0)),
            }
        )
    return orders


def preview_paper_rebalance(
    *,
    strategy_id: str | None = None,
    signal_id: str | None = None,
    profile: str = "demo",
    account_id: str = "paper",
    door: str = "rebalance_preview",
) -> dict:
    """Build the rebalance proposal and judge it with the policies in force."""
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
        signal_strategy = str(signal["strategy_id"])
        maximum_weight_limit = _project_max_weight(signal_strategy)
        held = store.list_paper_positions(account_id)
        held_symbols = [str(value).upper() for value in held["symbol"].tolist()] if not held.empty else []
        symbols = sorted(set(held_symbols) | set(targets))
        snapshot, policy = _build_snapshot(
            store,
            account_id=account_id,
            profile=profile,
            symbols=symbols,
            strategy_id=signal_strategy,
        )
        orders = _target_orders(snapshot, targets)
        verdict = evaluate(policy, snapshot, orders, door=door)
        _persist_pause(store, policy, snapshot)
        gross_target = float(sum(targets.values()))
        maximum_target = max(targets.values(), default=0.0)
        signal_ok = bool(targets)
        project_ok = maximum_target <= maximum_weight_limit + 1e-6
        prefix_blocked = not (signal_ok and project_ok)
        # The approval also binds the project-level constraint and the outcome it produced,
        # so an approval given while a project limit blocked the proposal cannot be replayed
        # after that limit moves.
        preview_id = hashlib.sha256(
            (
                f"{verdict.preview_id}|project_limit={maximum_weight_limit:.6f}"
                f"|ready={int(not prefix_blocked)}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        recorded = _record_evaluation(
            store,
            verdict,
            door=door,
            orders=orders,
            strategy_id=signal_strategy,
            signal_id=signal["id"],
            preview_id=preview_id,
        )
        account = store.paper_account(account_id)
    finally:
        store.close()

    signal_check = {
        "name": "signal_targets",
        "passed": signal_ok,
        "detail": f"{len(targets)} targets",
        "severity": "blocking",
    }
    project_check = {
        "name": "project_max_weight",
        "passed": project_ok,
        "detail": f"{maximum_target:.2%} / limit {maximum_weight_limit:.2%}",
        "severity": "blocking",
    }
    allowed = bool(verdict.allowed and not prefix_blocked)
    findings = [item.as_dict() for item in verdict.findings]
    if not project_check["passed"]:
        findings.insert(
            0,
            {
                "rule": "project_max_weight",
                "severity": "blocking",
                "message": (
                    f"Target weight {maximum_target:.2%} exceeds the project limit "
                    f"{maximum_weight_limit:.2%}."
                ),
                "observed": maximum_target,
                "limit": maximum_weight_limit,
                "symbol": None,
            },
        )
    if not signal_check["passed"]:
        findings.insert(
            0,
            {
                "rule": "signal_targets",
                "severity": "blocking",
                "message": "The signal contains no positive target weights.",
                "observed": 0.0,
                "limit": None,
                "symbol": None,
            },
        )
    blocking = [item for item in findings if item["severity"] == "blocking"]
    return {
        "account_id": account_id,
        "signal_id": signal["id"],
        "strategy_id": signal["strategy_id"],
        "profile": profile,
        "price_date": snapshot.price_date,
        "orders": orders,
        "preview_id": preview_id,
        "allowed": allowed,
        "risk_status": "ready" if allowed else "blocked",
        "checks": [signal_check, project_check, *list(verdict.checks)],
        "findings": findings,
        "blocking": blocking,
        "warnings": [item for item in findings if item["severity"] == "warning"],
        "info": [item for item in findings if item["severity"] == "info"],
        "policy": verdict.policy.to_json(),
        "snapshot_id": recorded["snapshot_id"],
        "evaluation_id": recorded["evaluation_id"],
        "metrics": verdict.metrics.as_dict(),
        "account": account,
        "projected": verdict.projected.as_dict(),
        "projected_cash": verdict.projected.cash,
        "turnover": verdict.projected.turnover,
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
    preview_id: str | None = None,
) -> dict:
    """Execute only what was previewed: the account is re-judged, never trusted."""
    if not confirm:
        raise PermissionError("Paper rebalance requires explicit confirmation")
    if not str(preview_id or "").strip():
        raise ValueError(
            "Paper rebalance requires the preview_id returned by /api/paper/rebalance/preview"
        )
    preview = preview_paper_rebalance(
        strategy_id=strategy_id,
        signal_id=signal_id,
        profile=profile,
        account_id=account_id,
        door="rebalance_execute",
    )
    if preview["preview_id"] != preview_id:
        store = ResultStore()
        try:
            detail = _stale_preview_detail(store, account_id, preview_id, preview)
        finally:
            store.close()
        raise StalePreviewError(detail)
    if not preview["allowed"]:
        raise RiskBlockedError(_blocked_detail_from_preview(preview))
    store = ResultStore()
    try:
        result = store.apply_paper_orders(
            preview["orders"],
            account_id=account_id,
            signal_id=preview["signal_id"],
            nav_date=preview["price_date"],
            risk_evaluation_id=preview["evaluation_id"],
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
        "evaluation_id": preview["evaluation_id"],
        "account": result["account"],
        "positions": positions.fillna("").to_dict("records"),
        "policy": preview["policy"],
    }


def _stale_preview_detail(
    store: ResultStore, account_id: str, approved_id: str, current: dict
) -> dict:
    previous = store.latest_risk_evaluation(account_id, preview_id=approved_id)
    changes: list[str] = []
    if previous is None:
        changes.append(
            f"no evaluation for preview {approved_id!r} was recorded for this account"
        )
    else:
        snapshot = store.get_risk_snapshot(previous["snapshot_id"])
        if snapshot is not None:
            if snapshot["price_date"] != current["price_date"]:
                changes.append(
                    f"prices moved from {snapshot['price_date']} to {current['price_date']}"
                )
            cash_now = float(current["account"]["cash"])
            if abs(float(snapshot["cash"]) - cash_now) > 1e-6:
                changes.append(
                    f"cash changed from {float(snapshot['cash']):,.2f} to {cash_now:,.2f}"
                )
        if previous["policy_version"] != int(current["policy"]["version"]):
            changes.append(
                f"the policy changed from v{previous['policy_version']} to "
                f"v{current['policy']['version']}"
            )
    if not changes:
        changes.append("the orders or account state no longer match the approved preview")
    return {
        "message": (
            "The approved preview is out of date: "
            + "; ".join(changes)
            + ". Run the preview again and approve the new one."
        ),
        "preview_id": approved_id,
        "current_preview_id": current["preview_id"],
        "changes": changes,
        "blocking": list(current.get("blocking") or []),
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
    """The manual order door: the same hard rules, whoever calls it."""
    engine = _engine(profile)
    if symbol not in engine.get_symbols():
        raise KeyError(symbol)
    if price is None:
        latest = engine.get_latest_date()
        bars = engine.get_bars([symbol], latest, latest, fields=["close"], use_cache=False)
        if bars.empty:
            raise MissingDataError(f"No bundled price for {symbol}")
        price = float(bars.iloc[-1]["close"])
    order_price = float(price)
    store = ResultStore()
    try:
        snapshot, policy = _build_snapshot(
            store,
            account_id=account_id,
            profile=profile,
            symbols=[symbol],
        )
        order = {
            "symbol": symbol,
            "action": action,
            "quantity": float(quantity),
            "price": order_price,
        }
        verdict = evaluate(policy, snapshot, [order], door="manual_order")
        _persist_pause(store, policy, snapshot)
        recorded = _record_evaluation(
            store,
            verdict,
            door="manual_order",
            orders=[order],
            signal_id=signal_id,
        )
        if not verdict.allowed:
            raise RiskBlockedError(_blocked_detail(verdict))
        order_id = store.save_paper_order(
            symbol,
            action,
            quantity,
            order_price,
            signal_id=signal_id,
            account_id=account_id,
            risk_evaluation_id=recorded["evaluation_id"],
        )
        row = (
            store.list_orders(limit=100, account_id=account_id)
            .loc[lambda frame: frame["id"].eq(order_id)]
            .iloc[0]
        )
    finally:
        store.close()
    return {
        **row.fillna("").to_dict(),
        "risk": {
            **recorded,
            "allowed": True,
            "policy": verdict.policy.to_json(),
            "warnings": [item.as_dict() for item in verdict.warnings],
            "info": [item.as_dict() for item in verdict.info],
            "checks": list(verdict.checks),
            "projected": verdict.projected.as_dict(),
        },
    }


def _price_history(
    profile: str, symbols: list[str], price_date: str | None, lookback_days: int
) -> dict[str, list[list]]:
    if not symbols or not price_date:
        return {}
    engine = _engine(profile)
    start = (
        pd.Timestamp(price_date) - pd.Timedelta(days=int(lookback_days * 1.7) + 45)
    ).strftime("%Y-%m-%d")
    bars = engine.get_bars(
        symbols,
        start,
        price_date,
        fields=["close"],
        strict=False,
        use_cache=False,
    )
    if bars.empty:
        return {}
    frame = bars[["date", "symbol", "close"]].dropna()
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    frame = frame.sort_values("date").groupby("symbol").tail(int(lookback_days))
    series: dict[str, list[list]] = {}
    for symbol, group in frame.groupby("symbol"):
        series[str(symbol).upper()] = [
            [str(row["date"]), float(row["close"])] for row in group.to_dict("records")
        ]
    return series


def paper_risk_analysis(
    *,
    account_id: str = "paper",
    profile: str = "demo",
    parameters: dict | None = None,
) -> dict:
    """Holdings risk for the account as it stands, replayed from frozen inputs."""
    parameters = HoldingsRiskParameters(**(parameters or {}))
    store = ResultStore()
    try:
        positions = store.list_paper_positions(account_id)
        symbols = (
            [str(value).upper() for value in positions["symbol"].tolist()]
            if not positions.empty
            else []
        )
        price_date, prices = _latest_prices(profile, symbols)
        account = store.mark_paper_positions(prices, price_date, account_id=account_id)
        positions = store.list_paper_positions(account_id)
    finally:
        store.close()

    holdings = []
    for row in positions.to_dict("records"):
        symbol = str(row["symbol"]).upper()
        price = prices.get(symbol)
        if price is None:
            market_price = row.get("market_price")
            price = float(market_price) if pd.notna(market_price) else None
        if price is None or price <= 0:
            continue
        quantity = float(row["quantity"])
        holdings.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "price": float(price),
                "market_value": quantity * float(price),
            }
        )
    series = _price_history(profile, [item["symbol"] for item in holdings], price_date, parameters.lookback_days)
    inputs = frozen_inputs(
        holdings=holdings,
        series=series,
        price_date=price_date,
        parameters=parameters,
        source=profile,
    )
    digest = input_hash(inputs)

    store = ResultStore()
    try:
        cached = store.get_risk_analysis(digest)
        if cached is not None:
            return {
                **cached["result"],
                "cached": True,
                "stored_at": cached["created_at"],
                "account": account,
            }
        result = analyse(inputs)
        store.save_risk_analysis(
            {
                "input_hash": digest,
                "account_id": account_id,
                "price_date": price_date,
                "inputs": inputs,
                "result": result,
            }
        )
    finally:
        store.close()
    return {**result, "cached": False, "account": account}


__all__ = [
    "RiskBlockedError",
    "StalePreviewError",
    "build_backtest_summary",
    "create_paper_order",
    "execute_paper_rebalance",
    "get_backtest",
    "get_backtest_event_page",
    "get_backtest_summary",
    "list_backtests",
    "list_paper_fills",
    "list_paper_orders",
    "list_paper_risk_evaluations",
    "paper_account_summary",
    "paper_risk_analysis",
    "paper_risk_policy",
    "preview_paper_rebalance",
    "publish_paper_risk_policy",
    "resume_paper_risk",
]
