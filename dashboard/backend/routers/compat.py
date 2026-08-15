"""Stable offline status endpoints used by the preserved workstation shell."""
from __future__ import annotations

from fastapi import APIRouter

from dashboard.backend.services.data_sync_service import get_health
from dashboard.backend.services.data_service import list_provider_status
from dashboard.backend.services.result_service import paper_account_summary

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/trading/status")
def trading_status() -> dict:
    return {
        "connected": False,
        "mode": "paper",
        "account_id": "paper",
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
    account = paper_account_summary("paper", "demo")["account"]
    return {
        "cash": account["cash"],
        "total_asset": account["equity"],
        "market_value": account["market_value"],
    }


@router.get("/data/status")
def data_status() -> dict:
    status = list_provider_status()
    return {
        "demo": {
            "latest_date": status["latest_date"],
            "needs_update": False,
            "provider_pending": False,
            "stock_count": status["symbol_count"],
            "phase_label": "bundled historical sample",
        },
        "runtime": {
            **status["profiles"]["runtime"],
            "needs_update": status["profiles"]["runtime"]["status"] != "ready",
        },
        "rq": get_health()["rq"],
        "realtime": {"connected": False, "status": "not_configured"},
    }


@router.get("/agent/config")
def agent_config() -> dict:
    return {
        "data_tools": {
            "configured": True,
            "available": True,
            "status": "ready",
            "count": 6,
        },
        "planner": {
            "configured": False,
            "available": False,
            "status": "not_configured",
        },
        "deterministic_workflow": {
            "configured": True,
            "available": True,
            "status": "ready",
            "steps": 6,
            "requires_paper_confirmation": True,
        },
        "llm": {
            "configured": False,
            "available": False,
            "provider": "none",
            "model": None,
            "mode": "not_configured",
        }
    }
