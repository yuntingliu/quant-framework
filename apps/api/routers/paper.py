"""Paper-only order and risk endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException

from alphalab.dataio import MissingDataError
from alphalab.execution.risk_policy import RiskPolicyError
from apps.api.models import (
    PaperOrderRequest,
    PaperRebalanceRequest,
    PaperResumeRequest,
    PaperRiskAnalysisRequest,
    PaperRiskPolicyRequest,
)
from apps.api.services.result_service import (
    RiskBlockedError,
    StalePreviewError,
    create_paper_order,
    execute_paper_rebalance,
    list_paper_fills,
    list_paper_orders,
    list_paper_risk_evaluations,
    paper_account_summary,
    paper_risk_analysis,
    paper_risk_policy,
    preview_paper_rebalance,
    publish_paper_risk_policy,
    resume_paper_risk,
)

router = APIRouter(prefix="/api/paper", tags=["paper"])


def _risk_exception(exc: ValueError) -> HTTPException:
    """Risk refusals are conflicts with a structured reason, not bad requests."""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return HTTPException(status_code=409, detail=detail)
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/orders")
def orders(limit: int = 100) -> list[dict]:
    return list_paper_orders(limit=limit)


@router.get("/account")
def account(
    account_id: str = "paper", profile: Literal["demo", "runtime"] = "runtime"
) -> dict:
    try:
        return paper_account_summary(account_id, profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/fills")
def fills(account_id: str = "paper", limit: int = 100) -> list[dict]:
    return list_paper_fills(account_id, limit)


@router.post("/rebalance/preview")
def preview(request: PaperRebalanceRequest) -> dict:
    try:
        return preview_paper_rebalance(
            strategy_id=request.strategy_id,
            signal_id=request.signal_id,
            profile=request.profile,
            account_id=request.account_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="signal not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/rebalance/execute")
def execute(request: PaperRebalanceRequest) -> dict:
    try:
        return execute_paper_rebalance(
            strategy_id=request.strategy_id,
            signal_id=request.signal_id,
            profile=request.profile,
            account_id=request.account_id,
            confirm=request.confirm,
            preview_id=request.preview_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RiskBlockedError, StalePreviewError) as exc:
        raise _risk_exception(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="signal not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/orders")
def submit_order(request: PaperOrderRequest) -> dict:
    try:
        return create_paper_order(
            request.symbol,
            request.action,
            request.quantity,
            request.price,
            request.signal_id,
            request.profile,
            request.account_id,
        )
    except RiskBlockedError as exc:
        raise _risk_exception(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="symbol not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/risk/policy")
def risk_policy(account_id: str = "paper", strategy_id: str | None = None) -> dict:
    try:
        return paper_risk_policy(account_id, strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/risk/policy")
def publish_risk_policy(request: PaperRiskPolicyRequest) -> dict:
    payload = request.model_dump(exclude_none=True)
    # Transport fields are not part of a policy version.
    payload.pop("account_id", None)
    payload.pop("strategy_id", None)
    try:
        return publish_paper_risk_policy(
            payload,
            account_id=request.account_id,
            strategy_id=request.strategy_id,
        )
    except RiskPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/risk/evaluations")
def risk_evaluations(account_id: str = "paper", limit: int = 50) -> list[dict]:
    return list_paper_risk_evaluations(account_id, limit=limit)


@router.post("/risk/analysis")
def risk_analysis(request: PaperRiskAnalysisRequest) -> dict:
    try:
        return paper_risk_analysis(
            account_id=request.account_id,
            profile=request.profile,
            parameters=request.parameters,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/risk/resume")
def risk_resume(request: PaperResumeRequest) -> dict:
    try:
        return resume_paper_risk(request.account_id, request.reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
