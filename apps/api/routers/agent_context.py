"""Read-only context endpoint for the optional Conexus Research Agent."""

from __future__ import annotations

from fastapi import APIRouter, Query

from apps.api.services.agent_context_service import workspace_context

router = APIRouter(prefix="/api/agent", tags=["agent-context"])


@router.get("/context")
def context(
    backtest_limit: int = Query(default=5, ge=1, le=20),
    sync_job_limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    return workspace_context(
        backtest_limit=backtest_limit,
        sync_job_limit=sync_job_limit,
    )
