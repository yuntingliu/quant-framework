"""Small read-only snapshot used by the optional research Agent."""

from __future__ import annotations

from copy import deepcopy
from threading import Lock
from time import monotonic

from alphalab.dataio.catalog import DataCatalog
from dashboard.backend.services import data_sync_service, strategy_service
from dashboard.backend.services.result_service import list_backtests

_CACHE_SECONDS = 10.0
_catalog_lock = Lock()
_catalog_cached_at = 0.0
_catalog_cached_value: dict | None = None


def _catalog_summary(value: dict) -> dict:
    datasets = []
    for item in value.get("datasets", []):
        datasets.append(
            {
                key: item.get(key)
                for key in (
                    "id",
                    "label",
                    "status",
                    "configured",
                    "files",
                    "rows",
                    "bytes",
                    "date_start",
                    "date_end",
                    "symbol_count",
                    "error",
                )
                if key in item
            }
        )
    return {
        key: value.get(key)
        for key in ("status", "ready", "total", "configured")
        if key in value
    } | {"datasets": datasets}


def _factor_template_summary(value: dict) -> dict:
    return {
        key: value.get(key)
        for key in (
            "id",
            "label",
            "category",
            "description",
            "inputs",
            "requirements",
            "recommended_direction",
        )
        if key in value
    }


def _sync_job_summary(value: dict) -> dict:
    request = value.get("request") if isinstance(value.get("request"), dict) else {}
    symbols = request.get("symbols") if isinstance(request.get("symbols"), list) else []
    request_summary = {
        key: request.get(key)
        for key in (
            "kind",
            "project_id",
            "recipe_source_sha256",
            "datasets",
            "start",
            "end",
            "force",
            "source",
        )
        if key in request
    }
    if symbols:
        request_summary["symbol_count"] = len(symbols)
        request_summary["symbols_sample"] = symbols[:10]
    return {
        key: value.get(key)
        for key in (
            "id",
            "source",
            "status",
            "progress",
            "total",
            "message",
            "error",
            "cancel_requested",
            "created_at",
            "started_at",
            "finished_at",
        )
        if key in value
    } | {"request": request_summary}


def _runtime_catalog() -> dict:
    global _catalog_cached_at, _catalog_cached_value
    now = monotonic()
    with _catalog_lock:
        if _catalog_cached_value is None or now - _catalog_cached_at >= _CACHE_SECONDS:
            _catalog_cached_value = DataCatalog().summary()
            _catalog_cached_at = monotonic()
        return deepcopy(_catalog_cached_value)


def workspace_context(*, backtest_limit: int = 5, sync_job_limit: int = 5) -> dict:
    """Return one coherent Agent snapshot without repeated runtime-store scans."""

    runtime_catalog = _runtime_catalog()
    data_health = data_sync_service.get_health(runtime_summary=runtime_catalog)
    data_health.pop("runtime", None)
    factor_templates = strategy_service.factor_template_catalog().get("templates", [])
    sync_jobs = data_sync_service.jobs(limit=sync_job_limit)
    return {
        "profile": "runtime",
        "strategy_contract": "alphalab.sdk.v1",
        "data_contract": "alphalab.data_sdk.v1",
        "validation_contract": "alphalab.validation_sdk.v1",
        "data_health": data_health,
        "data_catalog": _catalog_summary(runtime_catalog),
        "fundamental_fields": [
            "shares",
            "market_cap",
            "ep",
            "bp",
            "roe",
            "gross_margin",
            "leverage",
            "profit_growth",
            "revenue_growth",
        ],
        "risk_factor_names": ["MKT", "SMB", "HML", "MOM", "RMW", "rf"],
        "projects": strategy_service.list_projects(),
        "factor_templates": [_factor_template_summary(item) for item in factor_templates],
        "sync_jobs": [_sync_job_summary(item) for item in sync_jobs],
        "backtests": list_backtests(limit=backtest_limit),
    }


__all__ = ["workspace_context"]
