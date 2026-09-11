"""Example backend half of a dashboard vertical slice.

Copy the route into a real router module, include that router in
``dashboard.backend.main``, and add contract tests before activating a widget.
"""
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/examples", tags=["examples"])


@router.get("/score")
def example_score(symbol: str = Query(min_length=8, max_length=9)) -> dict:
    return {
        "symbol": symbol.upper(),
        "score": 0.5,
        "asof_date": "2026-01-06",
        "status": "example",
    }
