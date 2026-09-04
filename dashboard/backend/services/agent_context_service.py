"""Small read-only snapshot used by the optional research Agent."""

from __future__ import annotations

import re
from copy import deepcopy
from threading import Lock
from time import monotonic

from alphalab.dataio.catalog import DataCatalog
from dashboard.backend.services import data_sync_service, strategy_service
from dashboard.backend.services.backtest_job_service import list_backtest_jobs
from dashboard.backend.services.result_service import list_backtests

_CACHE_SECONDS = 10.0
_catalog_lock = Lock()
_catalog_cached_at = 0.0
_catalog_cached_value: dict | None = None


def _safe_error_summary(value: object) -> str | None:
    if value in (None, ""):
        return None
    summary = str(value).splitlines()[0]
    summary = re.sub(r"[A-Za-z]:\\[^\s\"']+", "<internal-path>", summary)
    summary = re.sub(
        r"(?<![:/\w])/(?!/)(?:[^/\s]+/)+[^\s\"']+",
        "<internal-path>",
        summary,
    )
    summary = re.sub(r"\b[a-fA-F0-9]{40,64}\b", "<internal-id>", summary)
    return summary[:500]


def _catalog_summary(value: dict) -> dict:
    datasets = [
        {
            key: item.get(key)
            for key in (
                "id",
                "label",
                "status",
                "configured",
                "rows",
                "date_start",
                "date_end",
                "symbol_count",
            )
            if key in item
        }
        for item in value.get("datasets", [])
    ]
    return {
        key: value.get(key) for key in ("status", "ready", "total", "configured") if key in value
    } | {"datasets": datasets}


def _project_summary(value: dict) -> dict:
    return {
        key: value.get(key)
        for key in (
            "id",
            "name",
            "description",
            "profile",
            "current_revision",
            "dirty",
            "built_in",
            "editable",
            "updated_at",
        )
        if key in value
    } | {
        "factor_count": sum(
            1 for item in value.get("source_units", []) if item.get("kind") == "factor"
        )
    }


def _sync_job_summary(value: dict) -> dict:
    request = value.get("request") if isinstance(value.get("request"), dict) else {}
    symbols = request.get("symbols") if isinstance(request.get("symbols"), list) else []
    request_summary = {
        key: request.get(key)
        for key in ("kind", "project_id", "datasets", "start", "end", "force")
        if key in request
    }
    if symbols:
        request_summary["symbol_count"] = len(symbols)
        request_summary["symbols_sample"] = symbols[:5]
    summary = _safe_error_summary(value.get("error_summary") or value.get("error"))
    return {
        key: value.get(key)
        for key in (
            "id",
            "status",
            "progress",
            "total",
            "message",
            "cancel_requested",
            "created_at",
            "started_at",
            "finished_at",
        )
        if key in value
    } | {
        "error_code": value.get("error_code")
        or ("DATA_SYNC_FAILED" if value.get("status") == "failed" else None),
        "error_summary": summary,
        "request": request_summary,
    }


def _backtest_job_summary(value: dict) -> dict:
    request = value.get("request") if isinstance(value.get("request"), dict) else {}
    return {
        "status": value.get("status"),
        "id": value.get("id"),
        "result_id": value.get("result_id"),
        "error_code": value.get("error_code"),
        "error_summary": _safe_error_summary(value.get("error_summary")),
        "log_reference": value.get("log_reference"),
        "message": value.get("message"),
        "request": {
            key: request.get(key)
            for key in ("project_id", "start_date", "end_date", "revision")
            if key in request
        },
        "metrics": dict(value.get("metrics") or {}),
        "counts": dict(value.get("counts") or {}),
        "warnings": list(value.get("warnings") or ()),
        "research_valid": value.get("research_valid"),
        "research_invalid_reasons": list(value.get("research_invalid_reasons") or ()),
        "execution_fidelity": dict(value.get("execution_fidelity") or {}),
        "attempted_trade_count": int(value.get("attempted_trade_count") or 0),
        "successful_trade_count": int(value.get("successful_trade_count") or 0),
        "execution_data_fill_count": int(value.get("execution_data_fill_count") or 0),
        "synthetic_state_count": int(value.get("synthetic_state_count") or 0),
        "market_state_rejection_count": int(value.get("market_state_rejection_count") or 0),
        "suspension_rejection_count": int(value.get("suspension_rejection_count") or 0),
        "limit_up_rejection_count": int(value.get("limit_up_rejection_count") or 0),
        "limit_down_rejection_count": int(value.get("limit_down_rejection_count") or 0),
        "capacity_rejection_count": int(value.get("capacity_rejection_count") or 0),
        "cash_rejection_count": int(value.get("cash_rejection_count") or 0),
        "created_at": value.get("created_at"),
        "finished_at": value.get("finished_at"),
    }


def _backtest_summary(value: dict) -> dict:
    return {
        key: value.get(key)
        for key in (
            "id",
            "strategy_id",
            "profile",
            "start_date",
            "end_date",
            "total_return",
            "annual_return",
            "annual_vol",
            "sharpe",
            "max_drawdown",
            "n_periods",
            "run_at",
        )
        if key in value
    }


def _runtime_catalog() -> dict:
    global _catalog_cached_at, _catalog_cached_value
    now = monotonic()
    with _catalog_lock:
        if _catalog_cached_value is None or now - _catalog_cached_at >= _CACHE_SECONDS:
            _catalog_cached_value = DataCatalog().summary()
            _catalog_cached_at = monotonic()
        return deepcopy(_catalog_cached_value)


def workspace_context(*, backtest_limit: int = 5, sync_job_limit: int = 5) -> dict:
    """Return project, runtime-data, and task summaries without source or templates."""

    runtime_catalog = _runtime_catalog()
    data_health = data_sync_service.get_health(runtime_summary=runtime_catalog)
    rq_status = data_health.get("rq") if isinstance(data_health.get("rq"), dict) else {}
    return {
        "status": "ready",
        "profile": "runtime",
        "data_status": {
            "status": data_health.get("status"),
            "rq": {
                "status": rq_status.get("status"),
                "ready": rq_status.get("ready"),
            },
            "catalog": _catalog_summary(runtime_catalog),
        },
        "projects": [_project_summary(item) for item in strategy_service.list_projects()],
        "tasks": {
            "sync": [
                _sync_job_summary(item) for item in data_sync_service.jobs(limit=sync_job_limit)
            ],
            "backtest": [
                _backtest_job_summary(item) for item in list_backtest_jobs(limit=backtest_limit)
            ],
        },
        "recent_backtests": [
            _backtest_summary(item)
            for item in list_backtests(limit=backtest_limit)
            if item.get("profile") == "runtime"
        ],
    }


__all__ = ["workspace_context"]
