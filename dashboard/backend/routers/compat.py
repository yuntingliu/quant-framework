"""Stable offline status endpoints used by the preserved workstation shell."""
from __future__ import annotations

from fastapi import APIRouter

from dashboard.backend.services.framework_service import list_provider_status

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/trading/status")
def trading_status() -> dict:
    return {
        "connected": False,
        "mode": "paper",
        "account_id": None,
        "broker": "paper",
        "broker_label": "local simulator",
        "account_currency": "CNY",
        "readonly": False,
        "supports_real_orders": False,
        "supports_paper_orders": True,
        "can_submit": True,
        "session_state": "not_configured",
    }


@router.get("/trading/asset")
def trading_asset() -> dict:
    return {"cash": 1_000_000.0, "total_asset": 1_000_000.0, "market_value": 0.0}


@router.get("/data/status")
def data_status() -> dict:
    status = list_provider_status()
    return {
        "qmt": {
            "latest_date": status["latest_date"],
            "needs_update": False,
            "provider_pending": False,
            "live_connected": False,
            "stock_count": status["symbol_count"],
            "phase_label": "bundled historical sample",
        },
        "rq": {
            "latest_date": status["latest_date"],
            "needs_update": False,
            "provider_pending": False,
            "phase_label": "bundled point-in-time fundamentals",
        },
        "local": {
            "latest_date": status["latest_date"],
            "needs_update": False,
            "symbol_count": status["symbol_count"],
            "status": "ready",
        },
        "realtime": {"connected": False, "status": "not_configured"},
    }
