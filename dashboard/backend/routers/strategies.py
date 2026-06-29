"""Strategy template endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from dashboard.backend.services.framework_service import get_strategy_template, list_strategy_templates

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("")
def strategies() -> list[dict]:
    return list_strategy_templates()


@router.get("/{strategy_id}")
def strategy(strategy_id: str) -> dict:
    item = get_strategy_template(strategy_id)
    if item is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return item

